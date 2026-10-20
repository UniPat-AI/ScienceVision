"""Executable contracts for the molecular and medical measurement tools.

The page-level tests wait for the installed controllers. That makes a swallowed
installation error fail the test instead of merely logging to the browser console.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MOLSTAR = ROOT / "viewer" / "molstar.html"
NIIVUE = ROOT / "viewer" / "niivue.html"

MOLSTAR_STUB = """
  const subscribers = () => ({subscribe() { return {unsubscribe() {}}; }});
  const helper = {
    cropParams: {auto: true, padding: 7},
    relativeCrop: {x: 0, y: 0, width: 1, height: 1},
    behaviors: {
      cropParams: {next(value) { helper.cropParams = {...value}; }},
      relativeCrop: {next(value) { helper.relativeCrop = {...value}; }},
    },
    async getImageDataUri() { return 'data:image/png;base64,iVBORw0KGgo='; },
  };
  window.molstar = {Viewer: {async create(target, options) {
    window.__createOptions = {...options};
    const measurement = {
      state: {distances: [], angles: [], dihedrals: []},
      behaviors: {state: subscribers()},
      async addOrderLabels() {},
      async addDistance() { return {selection: {ref: 'distance-1'}}; },
      async addAngle() { return {selection: {ref: 'angle-1'}}; },
      async addDihedral() { return {selection: {ref: 'dihedral-1'}}; },
    };
    const selection = {
      additionsHistory: [],
      events: {additionsHistoryUpdated: subscribers()},
      modifyHistory() {},
    };
    const interactivity = {
      props: {granularity: 'residue'},
      setProps(value) { this.props = {...this.props, ...value}; },
      lociSelects: {deselectAll() {}, deselect() {}},
    };
    const cells = new Map();
    const canvas3d = {
      camera: {state: {
        mode: 'perspective',
        fov: Math.PI / 4,
        position: [12.5, -3.25, 40],
        target: [0.5, 1, 2],
        up: [0, 1, 0],
        radius: 18.75,
      }},
      props: {camera: {helper: {axes: {name: 'off', params: {}}}}},
      setProps(value) { this.props = {...this.props, ...value}; },
    };
    return {
      plugin: {
        helpers: {viewportScreenshot: helper},
        managers: {structure: {measurement, selection}, interactivity},
        state: {data: {cells, build() { return {delete() {}, async commit() {}}; }}},
        behaviors: {interaction: {selectionMode: subscribers()}},
        canvas3d,
        selectionMode: false,
      },
      async loadStructureFromUrl() {},
      async loadStructureFromData() {},
    };
  }}};
"""

NIIVUE_STUB = """
  window.niivue = {Niivue: class {
    constructor(options) {
      this.opts = {...options};
      this.scene = {crosshairPos: [0.5, 0.5, 0.5], renderAzimuth: 110, renderElevation: 15};
      this.volumes = [];
      this.screenSlices = [];
      this.initialized = true;
      this.uiData = {dpr: 1};
      this.gl = {finish() {}};
      this.onLocationChange = null;
      window.__nvInstance = this;
    }
    attachTo(id) {
      this.canvas = document.getElementById(id);
      this.canvas.width = 200;
      this.canvas.height = 200;
      this.canvas.getBoundingClientRect = () => ({left: 0, top: 0, width: 200, height: 200});
      this.canvas.setPointerCapture = () => {};
    }
    async loadVolumes() {
      this.volumes = [{hdr: {xyzt_units: 2, dims: [3, 101, 101, 101]}, dimsRAS: [100, 100, 100]}];
      this.screenSlices = [{
        axCorSag: 0,
        sliceFrac: 0.5,
        leftTopWidthHeight: [0, 0, 200, 200],
        leftTopMM: [0, 0],
        fovMM: [100, 100],
      }];
    }
    setSliceType(value) { this.opts.sliceType = value; this.drawScene(); }
    drawScene() {}
    drawCircle() {}
    drawLine() {}
    drawTextBetween() {}
    frac2vox(frac) { return frac.map(value => Math.round(value * 100)); }
    frac2mm(frac) { return frac.map(value => value * 100); }
    tileIndex() { return 0; }
    screenXY2TextureFrac(x, y) { return [x / 200, y / 200, 0.5]; }
    createOnLocationChange() {
      if (typeof this.onLocationChange === 'function') this.onLocationChange();
    }
  }};
"""


def test_measurement_resources_are_wired_into_each_viewer() -> None:
    pages = {
        MOLSTAR: (
            "viewer/shared/molstar-measurements.css",
            "viewer/shared/molstar-measurements.js",
        ),
        NIIVUE: (
            "viewer/shared/niivue-measurements.css",
            "viewer/shared/niivue-measurements.js",
        ),
    }
    for page, resources in pages.items():
        source = page.read_text(encoding="utf-8")
        for resource in resources:
            assert (ROOT / resource).is_file()
            assert f'/{resource}' in source


def test_the_structure_viewer_offers_the_selection_mode_toggle() -> None:
    source = MOLSTAR.read_text(encoding="utf-8")
    assert "viewportShowSelectionMode: true" in source, (
        "every Mol* measurement starts from a selection, so the viewport must "
        "keep its selection-mode toggle visible"
    )


def test_molstar_controller_installs_and_exposes_all_modes(viewer_server: str) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.viewer.test_screenshot_contract import _launch_browser

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        page = browser.new_page()
        page.route(
            "**/viewer/shared/vendor/molstar/4.5.0/build/viewer/molstar.js",
            lambda route: route.fulfill(
                content_type="application/javascript", body=MOLSTAR_STUB
            ),
        )
        page.goto(
            f"{viewer_server}/viewer/molstar.html?file=/data/model.pdb&type=pdb",
            wait_until="load",
        )
        page.wait_for_function("window.__svMolstarMeasurements !== undefined")
        options = page.evaluate("window.__createOptions")
        assert options["viewportShowSelectionMode"] is True, (
            "the flag has to reach Viewer.create, not merely appear in the file"
        )
        result = page.evaluate("""async () => {
          const controller = window.__svMolstarMeasurements;
          const modes = [];
          for (const mode of ['distance', 'angle', 'dihedral']) {
            await controller.setMode(mode);
            modes.push(controller.getState().mode);
          }
          const buffer = new ArrayBuffer(8);
          const tuple = new Int32Array(buffer);
          tuple[0] = 17;
          tuple[1] = 18;
          const packedSingleton = new Float64Array(buffer)[0];
          return {
            modes,
            packed: window.ScienceVisionMolstarMeasurementMath.orderedSetInfo(packedSingleton),
            sorted: window.ScienceVisionMolstarMeasurementMath.orderedSetInfo(new Int32Array([23])),
            panelModes: Array.from(document.querySelectorAll(
              '#sv-mol-measure button[data-mode]'
            ), button => button.dataset.mode),
          };
        }""")
        assert result == {
            "modes": ["distance", "angle", "dihedral"],
            "packed": {"first": 17, "size": 1},
            "sorted": {"first": 23, "size": 1},
            "panelModes": ["navigate", "distance", "angle", "dihedral"],
        }
        browser.close()


def test_niivue_controller_disables_3d_and_exports_length_and_angle(
    viewer_server: str,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.viewer.test_screenshot_contract import _launch_browser

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        page = browser.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.route(
            "**/viewer/shared/vendor/@niivue/niivue/0.45.0/dist/niivue.umd.js",
            lambda route: route.fulfill(
                content_type="application/javascript", body=NIIVUE_STUB
            ),
        )
        page.goto(
            f"{viewer_server}/viewer/niivue.html?file=/data/volume.nii.gz",
            wait_until="load",
        )
        page.wait_for_function("window.__svNiiVueMeasurements !== undefined")

        result = page.evaluate("""async () => {
          const controller = window.__svNiiVueMeasurements;
          const canvas = document.getElementById('gl');
          const fire = (type, x, y, pointerId) => canvas.dispatchEvent(new PointerEvent(type, {
            bubbles: true,
            cancelable: true,
            pointerId,
            button: 0,
            buttons: type === 'pointerdown' ? 1 : 0,
            clientX: x,
            clientY: y,
          }));
          const click = (x, y, pointerId) => {
            fire('pointerdown', x, y, pointerId);
            fire('pointerup', x, y, pointerId);
          };

          document.querySelector('#view-toggle [data-mode="render"]').click();
          const renderDisabled = {
            length: document.querySelector('[data-measure-mode="length"]').disabled,
            angle: document.querySelector('[data-measure-mode="angle"]').disabled,
          };
          controller.setMode('length');
          const renderMode = document.querySelector(
            '#sv-niivue-measure-panel [data-measure-mode].active'
          ).dataset.measureMode;

          document.querySelector('#view-toggle [data-mode="multiplanar"]').click();
          controller.setMode('length');
          click(20, 20, 1);
          click(80, 20, 2);
          controller.setMode('angle');
          click(20, 80, 3);
          click(50, 50, 4);
          click(80, 80, 5);
          return {
            renderDisabled,
            renderMode,
            measurements: controller.list(),
            described: window.ScienceVisionViewerScreenshot.describeView().measurements,
          };
        }""")

        assert result["renderDisabled"] == {"length": True, "angle": True}
        assert result["renderMode"] == "navigate"
        assert [item["type"] for item in result["measurements"]["items"]] == [
            "length",
            "angle",
        ]
        assert result["measurements"]["items"][0]["length_mm"] == pytest.approx(30)
        assert result["measurements"]["items"][1]["angle_deg"] == pytest.approx(90)
        assert result["described"] == result["measurements"]
        assert page_errors == []
        browser.close()
