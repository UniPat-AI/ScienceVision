// tools/cad_qa_pack/viewer/viewer3d.js
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

// Viewer HTML lives at <pack>/viewer/; manifest resource paths are stored
// relative to the pack root (see manifest.json §3), so prepend this when
// turning a manifest path into a URL.
const ASSET_BASE = "../";

const VIEWS = {
  Front: [0, 0, 1], Back: [0, 0, -1], Left: [-1, 0, 0],
  Right: [1, 0, 0], Top: [0, 1, 0], Bottom: [0, -1, 0],
};

export function initViewer(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.localClippingEnabled = true;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.1;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  // Force one layout pass so the canvas has real dimensions before the first
  // setSize — otherwise (grid/flex not yet resolved) we can size the drawing
  // buffer to 0×0 and the canvas presents nothing to the compositor. This is
  // the sizing pattern the known-good /viewer/gltf.html uses.
  canvas.getBoundingClientRect();
  renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x272b33);

  // The GLB materials are fully metallic (metalness=1) with no texture; without
  // an environment to reflect they render as a flat near-black blob (looks like
  // "no 3D"). Use the same RoomEnvironment PMREM the working mesh viewer uses so
  // parts read as solid, well-lit metal, on a dark background for contrast.
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

  const camera = new THREE.PerspectiveCamera(
    45, (canvas.clientWidth || 1) / (canvas.clientHeight || 1), 0.01, 5000);
  const controls = new OrbitControls(camera, canvas);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x33383f, 0.6));
  const dir = new THREE.DirectionalLight(0xffffff, 1.6); dir.position.set(1, 1.4, 1); scene.add(dir);
  const dir2 = new THREE.DirectionalLight(0xffffff, 0.5); dir2.position.set(-1, 0.4, -0.8); scene.add(dir2);

  const clipPlane = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0);
  let current = null, radius = 1, center = new THREE.Vector3(), clipEnabled = false;
  let wireframeOn = false, doubleSideOn = false;

  function resize() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (w > 0 && h > 0 && (canvas.width !== Math.floor(w * renderer.getPixelRatio()) ||
                           canvas.height !== Math.floor(h * renderer.getPixelRatio()))) {
      renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    }
  }
  // setAnimationLoop (not a hand-rolled requestAnimationFrame): the render is
  // driven by the compositor so every frame is presented; matches gltf.html.
  renderer.setAnimationLoop(() => { resize(); controls.update(); renderer.render(scene, camera); });
  window.addEventListener("resize", resize);

  function frame(dirVec) {
    const d = radius * 2.6;
    camera.position.copy(center).add(new THREE.Vector3(...dirVec).multiplyScalar(d || 1));
    camera.near = radius / 100; camera.far = radius * 100; camera.updateProjectionMatrix();
    controls.target.copy(center); controls.update();
  }

  const MAP_KEYS = ["map", "normalMap", "roughnessMap", "metalnessMap", "aoMap", "emissiveMap", "alphaMap", "bumpMap", "displacementMap", "envMap"];
  function disposeMaterial(m) {
    if (!m) return;
    MAP_KEYS.forEach(k => { if (m[k]) m[k].dispose(); });
    m.dispose();
  }
  function clearScene() {
    if (current) {
      scene.remove(current);
      current.traverse(o => {
        if (o.geometry) o.geometry.dispose();
        if (o.isMesh && o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(disposeMaterial);
      });
      current = null;
    }
  }

  async function load(record) {
    hideError();
    clearScene();
    const gltf = await new GLTFLoader().loadAsync(ASSET_BASE + record.glb);
    current = gltf.scene;
    scene.add(current);
    const box = new THREE.Box3().setFromObject(current);
    box.getCenter(center); radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-3);
    applyClip();
    setWireframe(wireframeOn);
    setDoubleSide(doubleSideOn);
    frame([1, 0.7, 1]);
  }

  function eachMat(fn) { if (current) current.traverse(o => { if (o.isMesh) (Array.isArray(o.material) ? o.material : [o.material]).forEach(fn); }); }
  function setWireframe(on) { eachMat(m => (m.wireframe = on)); }
  function setDoubleSide(on) { eachMat(m => (m.side = on ? THREE.DoubleSide : THREE.FrontSide)); }
  function applyClip() {
    const ax = document.getElementById("clip-axis").value;
    const t = parseFloat(document.getElementById("clip").value);
    clipPlane.normal.set(ax === "x" ? -1 : 0, ax === "y" ? -1 : 0, ax === "z" ? -1 : 0);
    const along = ax === "x" ? center.x : ax === "y" ? center.y : center.z;
    clipPlane.constant = along + t * radius;
    eachMat(m => { m.clippingPlanes = clipEnabled ? [clipPlane] : []; m.needsUpdate = true; });
  }

  // toolbar wiring
  const vb = document.getElementById("view-buttons");
  Object.keys(VIEWS).forEach(name => { const b = document.createElement("button"); b.textContent = name; b.onclick = () => frame(VIEWS[name]); vb.appendChild(b); });
  document.querySelector('[data-op="wireframe"]').onclick = (e) => { wireframeOn = e.target.classList.toggle("on"); setWireframe(wireframeOn); };
  document.querySelector('[data-op="doubleside"]').onclick = (e) => { doubleSideOn = e.target.classList.toggle("on"); setDoubleSide(doubleSideOn); };
  document.querySelector('[data-op="reset"]').onclick = () => frame([1, 0.7, 1]);
  document.getElementById("clip").oninput = () => { clipEnabled = true; applyClip(); };
  document.getElementById("clip-axis").onchange = () => { clipEnabled = true; applyClip(); };

  function showError(record, err) {
    const el = document.getElementById("load-error"); el.hidden = false;
    el.innerHTML = "";
    const msg = document.createElement("div");
    msg.textContent = `Failed to load 3D model: ${err}`;
    el.appendChild(msg);
    const views = record.views || [];
    if (views.length) {
      const label = document.createElement("div");
      label.textContent = "Reference views:";
      el.appendChild(label);
      views.slice(0, 3).forEach(v => {
        const img = document.createElement("img");
        img.src = ASSET_BASE + v;
        el.appendChild(img);
      });
    }
  }
  function hideError() { const el = document.getElementById("load-error"); el.hidden = true; el.innerHTML = ""; }

  return { load, showError };
}
