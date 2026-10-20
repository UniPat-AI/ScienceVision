(function installScienceVisionViewerMCP() {
  'use strict';

  const MAX_DEPTH = 20;
  const MAX_NODES = 10000;
  const MAX_STRING_LENGTH = 1000000;
  let provider = null;
  let tools = null;
  let toolMap = null;
  let loadError = null;
  let settleReadiness;
  let callQueue = Promise.resolve();
  const readiness = new Promise(resolve => { settleReadiness = resolve; });

  function fail(message) {
    throw new Error(message);
  }

  function cloneJSON(value, path = '$', depth = 0, budget = { count: 0 }) {
    budget.count += 1;
    if (budget.count > MAX_NODES) fail('MCP value is too large.');
    if (depth > MAX_DEPTH) fail('MCP value is nested too deeply.');
    if (value === null || typeof value === 'boolean') return value;
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) fail(`${path} must be a finite number.`);
      return value;
    }
    if (typeof value === 'string') {
      if (value.length > MAX_STRING_LENGTH) fail(`${path} is too long.`);
      return value;
    }
    if (Array.isArray(value)) {
      const descriptors = Object.getOwnPropertyDescriptors(value);
      const output = [];
      for (let index = 0; index < value.length; index += 1) {
        const descriptor = descriptors[index];
        if (!descriptor || !Object.prototype.hasOwnProperty.call(descriptor, 'value')) {
          fail(`${path} must be a dense data-only array.`);
        }
        output.push(cloneJSON(descriptor.value, `${path}[${index}]`, depth + 1, budget));
      }
      return output;
    }
    if (typeof value !== 'object') fail(`${path} is not JSON-compatible.`);
    const prototype = Object.getPrototypeOf(value);
    // Same-origin parent/iframe calls use different Object.prototype identities.
    if (prototype !== null && prototype !== Object.prototype
        && Object.getPrototypeOf(prototype) !== null) fail(`${path} must be a plain object.`);
    const descriptors = Object.getOwnPropertyDescriptors(value);
    const output = Object.create(null);
    for (const key of Object.keys(descriptors)) {
      const descriptor = descriptors[key];
      if (!descriptor.enumerable) continue;
      if (!Object.prototype.hasOwnProperty.call(descriptor, 'value')) {
        fail(`${path}.${key} must be a data property.`);
      }
      output[key] = cloneJSON(descriptor.value, `${path}.${key}`, depth + 1, budget);
    }
    return output;
  }

  function deepFreeze(value) {
    if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
    Object.freeze(value);
    for (const key of Object.keys(value)) deepFreeze(value[key]);
    return value;
  }

  function sameJSONValue(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function validateSchema(value, schema, path) {
    if (!schema || typeof schema !== 'object') fail(`Invalid schema at ${path}.`);
    if (Array.isArray(schema.oneOf)) {
      let matches = 0;
      let selected;
      for (const branch of schema.oneOf) {
        try {
          const candidate = cloneJSON(value);
          validateSchema(candidate, branch, path);
          matches += 1;
          selected = candidate;
        } catch (_) {
          // A oneOf branch that fails validation is expected.
        }
      }
      if (matches !== 1) fail(`${path} must match exactly one allowed shape.`);
      if (value && typeof value === 'object') {
        for (const key of Object.keys(value)) delete value[key];
        Object.assign(value, selected);
      }
    }
    if (schema.enum && !schema.enum.some(item => sameJSONValue(item, value))) {
      fail(`${path} must be one of: ${schema.enum.map(String).join(', ')}.`);
    }
    const type = schema.type;
    if (type === 'object') {
      if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`${path} must be an object.`);
      const properties = schema.properties || {};
      for (const [key, propertySchema] of Object.entries(properties)) {
        if (!Object.prototype.hasOwnProperty.call(value, key)
            && Object.prototype.hasOwnProperty.call(propertySchema, 'default')) {
          value[key] = cloneJSON(propertySchema.default, `${path}.${key}`);
        }
      }
      for (const key of schema.required || []) {
        if (!Object.prototype.hasOwnProperty.call(value, key)) fail(`${path}.${key} is required.`);
      }
      if (schema.additionalProperties === false) {
        const unknown = Object.keys(value).filter(key => !Object.prototype.hasOwnProperty.call(properties, key));
        if (unknown.length) fail(`${path} has unsupported parameter(s): ${unknown.join(', ')}.`);
      }
      for (const [key, item] of Object.entries(value)) {
        if (properties[key]) validateSchema(item, properties[key], `${path}.${key}`);
      }
      if (Number.isInteger(schema.minProperties) && Object.keys(value).length < schema.minProperties) {
        fail(`${path} must contain at least ${schema.minProperties} parameter(s).`);
      }
      return;
    }
    if (type === 'array') {
      if (!Array.isArray(value)) fail(`${path} must be an array.`);
      if (Number.isInteger(schema.minItems) && value.length < schema.minItems) {
        fail(`${path} must contain at least ${schema.minItems} item(s).`);
      }
      if (Number.isInteger(schema.maxItems) && value.length > schema.maxItems) {
        fail(`${path} must contain at most ${schema.maxItems} item(s).`);
      }
      if (schema.items) value.forEach((item, index) => validateSchema(item, schema.items, `${path}[${index}]`));
      return;
    }
    if (type === 'string' && typeof value !== 'string') fail(`${path} must be a string.`);
    if (type === 'boolean' && typeof value !== 'boolean') fail(`${path} must be a boolean.`);
    if (type === 'number' || type === 'integer') {
      if (typeof value !== 'number' || !Number.isFinite(value)) fail(`${path} must be a finite number.`);
      if (type === 'integer' && !Number.isInteger(value)) fail(`${path} must be an integer.`);
      if (typeof schema.minimum === 'number' && value < schema.minimum) fail(`${path} must be >= ${schema.minimum}.`);
      if (typeof schema.maximum === 'number' && value > schema.maximum) fail(`${path} must be <= ${schema.maximum}.`);
      if (typeof schema.exclusiveMinimum === 'number' && value <= schema.exclusiveMinimum) {
        fail(`${path} must be > ${schema.exclusiveMinimum}.`);
      }
    }
  }

  function normalizeTools(candidateTools) {
    if (!Array.isArray(candidateTools) || !candidateTools.length) fail('MCP provider must expose at least one tool.');
    const names = new Set();
    const normalized = candidateTools.map((tool, index) => {
      const copy = cloneJSON(tool, `tools[${index}]`);
      if (!copy || typeof copy.name !== 'string' || !/^(?:viewer|sv3d)_[a-z0-9_]+$/.test(copy.name)) {
        fail(`tools[${index}].name is invalid.`);
      }
      if (names.has(copy.name)) fail(`Duplicate MCP tool: ${copy.name}.`);
      names.add(copy.name);
      if (typeof copy.description !== 'string' || !copy.description) fail(`${copy.name} needs a description.`);
      if (!copy.inputSchema || copy.inputSchema.type !== 'object') fail(`${copy.name} needs an object inputSchema.`);
      return deepFreeze(copy);
    });
    return deepFreeze(normalized);
  }

  const api = Object.freeze({
    version: 1,
    get ready() {
      return Boolean(provider && tools && !loadError);
    },
    async whenReady() {
      const result = await readiness;
      if (result.error) throw result.error;
      return true;
    },
    listTools() {
      if (loadError) throw loadError;
      if (!tools) throw new Error('Viewer MCP tools are still loading.');
      return cloneJSON(tools);
    },
    callTool(name, args = {}, context) {
      const execute = async () => {
        if (loadError) throw loadError;
        if (!provider || !toolMap) throw new Error('Viewer MCP tools are still loading.');
        if (typeof name !== 'string' || !toolMap.has(name)) throw new Error(`Unknown viewer MCP tool: ${String(name)}.`);
        const safeArgs = cloneJSON(args, 'arguments');
        validateSchema(safeArgs, toolMap.get(name).inputSchema, 'arguments');
        const replay = context && context.replay === true ? {replay: true} : undefined;
        try {
          return cloneJSON(await provider.callTool(name, safeArgs, replay), 'result');
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          throw new Error(`${name} failed: ${message}`);
        }
      };
      const result = callQueue.then(execute, execute);
      callQueue = result.then(() => undefined, () => undefined);
      return result;
    },
  });

  const registration = Object.freeze({
    ready(nextProvider) {
      if (provider || loadError) return;
      try {
        if (!nextProvider || typeof nextProvider.callTool !== 'function') fail('MCP provider is invalid.');
        const nextTools = normalizeTools(nextProvider.tools);
        provider = Object.freeze({ callTool: nextProvider.callTool.bind(nextProvider) });
        tools = nextTools;
        toolMap = new Map(nextTools.map(tool => [tool.name, tool]));
        settleReadiness({ ready: true });
      } catch (error) {
        registration.fail(error);
      }
    },
    fail(error) {
      if (provider || loadError) return;
      const message = error instanceof Error ? error.message : String(error);
      loadError = new Error(`Viewer MCP is unavailable: ${message}`);
      settleReadiness({ error: loadError });
    },
  });

  Object.defineProperty(window, 'ScienceVisionViewerMCP', {
    configurable: false,
    enumerable: true,
    value: api,
    writable: false,
  });
  Object.defineProperty(window, '__ScienceVisionViewerMCPProvider', {
    configurable: false,
    enumerable: false,
    value: registration,
    writable: false,
  });
}());
