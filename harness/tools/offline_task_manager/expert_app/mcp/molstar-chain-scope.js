// 「显示链」的链作用域：Mol* 原生组件树的一层薄封装。
//
// 只看链 X = 用一句 MolScript 表达式建出链 X 的组件，在它上面套 Mol* 自己的
// 表示预设（Mol* 会在下面建出 Polymer / Ligand / Water / … 子组件），再把根部
// 各类组件隐藏。显示全部 = 移除链组件、恢复根部。不碰 Current Selection，不碰
// 鼠标 loci，不重新解析 CIF；重放时无头运行也是确定的。
//
// 是否隔离不另存变量，一切从 plugin.state.data 推导（有没有 label 以 `sv-chain:`
// 开头的组件），所以 sv3d_set_symmetry 重建层级把链组件冲掉后自然回到全显。
//
// 设计：docs/superpowers/specs/2026-09-03-molstar-chain-scope-design.md
(function installScienceVisionMolstarChainScope(global) {
  'use strict';

  if (global.ScienceVisionMolstarChainScope) return;

  const CHAIN_LABEL_PREFIX = 'sv-chain:';
  const CHAIN_KEY_PREFIX = 'sv-chain-';
  // Glycans are a handful of residues deep; eight covalent layers covers them
  // without walking far along anything else that happens to be bonded.
  const GLYCAN_LAYER_COUNT = 8;
  const COMPONENT_TRANSFORMER = 'ms-plugin.structure-component';
  const REPRESENTATION_TRANSFORMER = 'ms-plugin.structure-representation-3d';

  // ---- MolScript literals ---------------------------------------------------
  // The viewer bundle does not export MolScriptBuilder; expressions are plain
  // {head: {name}, args} objects. Symbol ids verified against molstar 4.5.0.
  function symbol(name) { return {name}; }
  function apply(name, args) { return args === undefined ? {head: symbol(name)} : {head: symbol(name), args}; }
  const authAsymId = () => apply('structure-query.atom-property.macromolecular.auth_asym_id');
  const atomGroupsByChain = test => apply('structure-query.generator.atom-groups', {'chain-test': test});
  const chainIs = chainId => atomGroupsByChain(apply('core.rel.eq', [authAsymId(), chainId]));
  const chainIn = chainIds => atomGroupsByChain(
    apply('core.set.has', [apply('core.type.set', chainIds), authAsymId()]),
  );
  const includeConnected = base => apply('structure-query.modifier.include-connected', {
    0: base, 'layer-count': GLYCAN_LAYER_COUNT, 'as-whole-residues': true,
  });
  const intersectBy = (base, by) => apply('structure-query.modifier.intersect-by', {0: base, by});
  const merge = parts => apply('structure-query.combinator.merge', parts);

  // Chain X plus whatever glycan residues are covalently attached to it. The
  // attached sugars live in their own auth chains (RCSB convention), so
  // include-connected walks the bond and the intersect keeps it to sugars only:
  // an inter-chain disulfide must not drag the partner chain in.
  function chainExpression(chainId, glycanChainIds) {
    const base = chainIs(chainId);
    if (!glycanChainIds.length) return base;
    return merge([base, intersectBy(includeConnected(base), chainIn(glycanChainIds))]);
  }

  // ---- hierarchy readers ----------------------------------------------------
  function currentStructures(plugin) {
    const structures = plugin?.managers?.structure?.hierarchy?.current?.structures;
    return Array.isArray(structures) ? structures : [];
  }

  function componentLabel(ref) {
    return String(ref?.cell?.obj?.label || '');
  }

  function isChainComponent(ref) {
    return componentLabel(ref).startsWith(CHAIN_LABEL_PREFIX);
  }

  function chainComponentsOf(structureRef) {
    return (structureRef.components || []).filter(isChainComponent);
  }

  function rootComponentsOf(structureRef) {
    return (structureRef.components || []).filter(ref => !isChainComponent(ref));
  }

  function authChainsInStructure(structure, visit) {
    const units = structure && structure.units;
    if (!units) return;
    for (const unit of units) {
      const hierarchy = unit.model && unit.model.atomicHierarchy;
      const entities = unit.model && unit.model.entities;
      if (!hierarchy || !entities || !unit.elements) continue;
      const chainIndexOf = hierarchy.chainAtomSegments.index;
      const residueIndexOf = hierarchy.residueAtomSegments.index;
      const authAsym = hierarchy.chains.auth_asym_id;
      const entityId = hierarchy.chains.label_entity_id;
      for (let i = 0; i < unit.elements.length; i += 1) {
        const element = unit.elements[i];
        const chainIndex = chainIndexOf[element];
        const entityIndex = entities.getEntityIndex(entityId.value(chainIndex));
        visit({
          chain_id: String(authAsym.value(chainIndex)),
          kind: String(entities.data.type.value(entityIndex)),
          residue_key: `${unit.model.id}:${residueIndexOf[element]}`,
        });
      }
    }
  }

  // Per auth chain: which entity kinds appear, how many atoms and polymer
  // residues. Read straight off the atomic hierarchy of every loaded structure
  // so the list needs no CIF re-fetch and stays right under any symmetry preset.
  function collectChains(plugin) {
    const rows = new Map();
    for (const structureRef of currentStructures(plugin)) {
      authChainsInStructure(structureRef?.cell?.obj?.data, atom => {
        let row = rows.get(atom.chain_id);
        if (!row) {
          row = {chain_id: atom.chain_id, kinds: new Set(), atom_count: 0, residues: new Set()};
          rows.set(atom.chain_id, row);
        }
        row.kinds.add(atom.kind);
        row.atom_count += 1;
        if (atom.kind === 'polymer') row.residues.add(atom.residue_key);
      });
    }
    return rows;
  }

  function glycanChainIds(rows) {
    return Array.from(rows.values())
      .filter(row => row.kinds.has('branched') && !row.kinds.has('polymer'))
      .map(row => row.chain_id)
      .sort();
  }

  function authChainsOfComponent(ref) {
    const found = new Set();
    authChainsInStructure(ref?.cell?.obj?.data, atom => found.add(atom.chain_id));
    return Array.from(found).sort();
  }

  // ---- component classification --------------------------------------------
  // The five classes the expert-facing tools speak of. Mol* names the sugar
  // component 'branched'; the expert word is carbohydrate. Anything else the
  // preset may add (nonStandard, lipid, coarse) is 'other' and left alone.
  const TYPE_BY_STATIC_KEY = Object.freeze({
    polymer: 'polymer', ligand: 'ligand', water: 'water', ion: 'ion', branched: 'carbohydrate',
  });

  function componentTypeOf(cell) {
    const type = cell?.transform?.params?.type;
    if (type && type.name === 'static') {
      return TYPE_BY_STATIC_KEY[String(type.params)] || 'other';
    }
    const label = String(cell?.obj?.label || '').toLowerCase();
    if (label.startsWith(CHAIN_LABEL_PREFIX)) return 'chain';
    if (/water/.test(label)) return 'water';
    if (/ligand/.test(label)) return 'ligand';
    if (/carbohydrate|glycan|branched/.test(label)) return 'carbohydrate';
    if (/polymer/.test(label)) return 'polymer';
    if (/\bion\b/.test(label)) return 'ion';
    return 'other';
  }

  function representationNames(plugin, componentRef) {
    return (componentRef.representations || []).map(rep => (
      String(rep?.cell?.transform?.params?.type?.name || '')
    )).filter(Boolean).sort();
  }

  // Mol*'s hierarchy manager does not list components nested under another
  // component; it only hangs their representations on the enclosing one. Read
  // the chain subtree from the state tree and wrap each child as the minimal
  // ref the component manager and hierarchy manager actually read:
  // `cell`, `representations[].cell`, `structure`.
  function nestedComponentRefs(plugin, chainRef, structureRef) {
    const tree = plugin.state?.data?.tree;
    const cells = plugin.state?.data?.cells;
    if (!tree || !cells) return [];
    const refs = [];
    const childRefs = tree.children.get(chainRef.cell.transform.ref);
    if (!childRefs) return refs;
    childRefs.forEach(childRef => {
      const cell = cells.get(childRef);
      if (!cell || cell.transform.transformer.id !== COMPONENT_TRANSFORMER) return;
      const representations = [];
      const grandChildren = tree.children.get(childRef);
      if (grandChildren) {
        grandChildren.forEach(repRef => {
          const repCell = cells.get(repRef);
          if (repCell && repCell.transform.transformer.id === REPRESENTATION_TRANSFORMER) {
            representations.push({kind: 'structure-representation', cell: repCell, version: repCell.transform.version});
          }
        });
      }
      refs.push({kind: 'structure-component', cell, version: cell.transform.version, structure: structureRef, representations});
    });
    return refs;
  }

  function describeScope(plugin, refs) {
    return refs.map(ref => ({
      type: componentTypeOf(ref.cell),
      label: componentLabel(ref),
      visible: !ref.cell?.state?.isHidden,
      representations: representationNames(plugin, ref),
      ref,
    })).filter(item => item.type !== 'chain');
  }

  // ---- Mol* mutations -------------------------------------------------------
  function toggleVisibility(plugin, refs, action) {
    const hierarchy = plugin.managers?.structure?.hierarchy;
    if (!refs.length || typeof hierarchy?.toggleVisibility !== 'function') return;
    hierarchy.toggleVisibility(refs, action);
  }

  async function removeRefs(plugin, refs) {
    const hierarchy = plugin.managers?.structure?.hierarchy;
    if (!refs.length || typeof hierarchy?.remove !== 'function') return;
    await hierarchy.remove(refs, true);
  }

  // Copy per-class visibility and representation from one scope onto another.
  // Component state is one logical state; the scope only decides what is on
  // screen. Classes missing on the target are skipped (a chain with no ions
  // has nothing to mirror ions onto).
  async function mirrorTypeState(plugin, fromEntries, toEntries) {
    const manager = plugin.managers?.structure?.component;
    const byType = new Map();
    for (const entry of fromEntries) {
      if (entry.type === 'other') continue;
      if (!byType.has(entry.type)) byType.set(entry.type, entry);
    }
    for (const target of toEntries) {
      const source = byType.get(target.type);
      if (!source) continue;
      if (source.representations.join('|') !== target.representations.join('|')
          && manager && typeof manager.addRepresentation === 'function') {
        if (typeof manager.removeRepresentations === 'function') {
          await manager.removeRepresentations([target.ref]);
        }
        for (const name of source.representations) {
          await manager.addRepresentation([target.ref], name);
        }
      }
      // A freshly added representation is shown regardless of the component's
      // hidden flag, so re-assert visibility after restyling: show, then hide
      // if the source is hidden, mirrors the adapter's own restore quirk.
      toggleVisibility(plugin, [target.ref], 'show');
      if (!source.visible) toggleVisibility(plugin, [target.ref], 'hide');
    }
  }

  function create(plugin) {
    if (!plugin) throw new Error('ChainScope needs a Mol* plugin.');

    function isolatedChainId() {
      for (const structureRef of currentStructures(plugin)) {
        const chain = chainComponentsOf(structureRef)[0];
        if (chain) return componentLabel(chain).slice(CHAIN_LABEL_PREFIX.length);
      }
      return null;
    }

    function list() {
      const isolated = isolatedChainId();
      const glycansOfIsolated = new Set();
      if (isolated) {
        for (const structureRef of currentStructures(plugin)) {
          for (const chain of chainComponentsOf(structureRef)) {
            for (const id of authChainsOfComponent(chain)) {
              if (id !== isolated) glycansOfIsolated.add(id);
            }
          }
        }
      }
      return Array.from(collectChains(plugin).values())
        .filter(row => row.kinds.has('polymer'))
        .sort((left, right) => left.chain_id.localeCompare(right.chain_id))
        .map(row => ({
          chain_id: row.chain_id,
          entity_kinds: Array.from(row.kinds).sort(),
          atom_count: row.atom_count,
          residue_count: row.residues.size,
          glycan_chains: row.chain_id === isolated ? Array.from(glycansOfIsolated).sort() : [],
          visible: isolated ? row.chain_id === isolated : true,
        }));
    }

    // Components of the active scope: the chain subtree while isolated, the
    // root preset components otherwise. Each entry: {type, label, visible,
    // representations, ref}; `ref` is what the hierarchy/component managers take.
    function scopeComponents() {
      const entries = [];
      for (const structureRef of currentStructures(plugin)) {
        const chains = chainComponentsOf(structureRef);
        if (chains.length) {
          for (const chain of chains) {
            entries.push(...describeScope(plugin, nestedComponentRefs(plugin, chain, structureRef)));
          }
        }
      }
      if (entries.length) return entries;
      for (const structureRef of currentStructures(plugin)) {
        entries.push(...describeScope(plugin, rootComponentsOf(structureRef)));
      }
      return entries;
    }

    async function showAll() {
      const removed = [];
      for (const structureRef of currentStructures(plugin)) {
        const chains = chainComponentsOf(structureRef);
        if (!chains.length) continue;
        removed.push(...chains);
        const chainState = [];
        for (const chain of chains) {
          chainState.push(...describeScope(plugin, nestedComponentRefs(plugin, chain, structureRef)));
        }
        const roots = describeScope(plugin, rootComponentsOf(structureRef));
        // Classes the chain never had (nothing to mirror from) simply come back.
        const mirrored = new Set(chainState.map(item => item.type));
        toggleVisibility(plugin, roots.filter(item => !mirrored.has(item.type)).map(item => item.ref), 'show');
        await mirrorTypeState(plugin, chainState, roots);
      }
      if (!removed.length) throw new Error('已经显示全部链，未再次切换。');
      await removeRefs(plugin, removed);
    }

    async function isolate(chainId) {
      const wanted = String(chainId == null ? '' : chainId);
      if (!wanted) throw new Error('only 需要链 ID。');
      const rows = collectChains(plugin);
      if (!Array.from(rows.values()).some(row => row.kinds.has('polymer'))) {
        throw new Error('当前结构没有链。');
      }
      const row = rows.get(wanted);
      if (!row) throw new Error(`当前结构没有链 ${wanted}。`);
      if (!row.kinds.has('polymer')) {
        throw new Error(`链 ${wanted} 是糖链，已随所连接的链一起显示。`);
      }
      const current = isolatedChainId();
      if (current === wanted) throw new Error(`已经只看链 ${wanted}，未再次切换。`);
      if (current) await showAll();

      const builders = plugin.builders?.structure;
      if (typeof builders?.tryCreateComponentFromExpression !== 'function'
          || typeof builders?.representation?.applyPreset !== 'function') {
        throw new Error('无法只看链：Mol* 未提供组件构建器。');
      }
      const expression = chainExpression(wanted, glycanChainIds(rows));
      const created = [];
      const hiddenRoots = [];
      try {
        for (const structureRef of currentStructures(plugin)) {
          let hasChain = false;
          authChainsInStructure(structureRef?.cell?.obj?.data, atom => {
            if (atom.chain_id === wanted) hasChain = true;
          });
          if (!hasChain) continue;
          const component = await builders.tryCreateComponentFromExpression(
            structureRef.cell, expression, `${CHAIN_KEY_PREFIX}${wanted}`,
            {label: `${CHAIN_LABEL_PREFIX}${wanted}`},
          );
          if (!component) throw new Error(`无法只看链 ${wanted}：Mol* 未建出组件。`);
          created.push(component);
          await builders.representation.applyPreset(component, 'auto');
          // Root refs read BEFORE hiding: what the expert had set is what the
          // chain subtree must start from.
          const rootState = describeScope(plugin, rootComponentsOf(structureRef));
          const liveStructure = currentStructures(plugin)
            .find(item => item.cell?.transform?.ref === structureRef.cell?.transform?.ref) || structureRef;
          const chainRef = chainComponentsOf(liveStructure)
            .find(ref => ref.cell?.transform?.ref === component.ref);
          if (chainRef) {
            await mirrorTypeState(
              plugin, rootState, describeScope(plugin, nestedComponentRefs(plugin, chainRef, liveStructure)),
            );
          }
          const roots = rootComponentsOf(structureRef).filter(ref => !ref.cell?.state?.isHidden);
          hiddenRoots.push(...roots);
          toggleVisibility(plugin, roots, 'hide');
        }
        if (!created.length) throw new Error(`当前结构没有链 ${wanted}。`);
      } catch (error) {
        toggleVisibility(plugin, hiddenRoots, 'show');
        const refs = [];
        for (const structureRef of currentStructures(plugin)) refs.push(...chainComponentsOf(structureRef));
        await removeRefs(plugin, refs);
        throw error;
      }
    }

    return Object.freeze({list, isolatedChainId, isolate, showAll, scopeComponents});
  }

  Object.defineProperty(global, 'ScienceVisionMolstarChainScope', {
    configurable: false,
    enumerable: false,
    value: Object.freeze({create}),
    writable: false,
  });
}(typeof window !== 'undefined' ? window : globalThis));
