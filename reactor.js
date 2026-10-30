/* ============================================================
   FRIDAY — Reactor (Three.js)
   ============================================================ */

const STATE_PALETTE = {
  idle:      { primary: 0x00e5ff, secondary: 0x5ee7f2, accent: 0x8b5cf6, bloomStrength: 0.85, bloomThreshold: 0.85 },
  thinking:  { primary: 0xff9f00, secondary: 0xffce5c, accent: 0xff7b00, bloomStrength: 1.35, bloomThreshold: 0.7 },
  tooling:   { primary: 0x00ff88, secondary: 0x5effa1, accent: 0x00cc66, bloomStrength: 1.15, bloomThreshold: 0.75 },
  speaking:  { primary: 0xbc8cff, secondary: 0xd4b3ff, accent: 0xff5ce0, bloomStrength: 1.25, bloomThreshold: 0.75 },
  listening: { primary: 0x00ff88, secondary: 0x00e5ff, accent: 0x00b8d4, bloomStrength: 1.0, bloomThreshold: 0.82 },
  error:     { primary: 0xff2b4a, secondary: 0xff5c74, accent: 0xff0044, bloomStrength: 1.5, bloomThreshold: 0.65 }
};

let scene, camera, renderer, composer, bloomPass, controls, audioContext;
let reactorGroup, coreMesh, coreMat, haloMesh, orbitalParticles, reactorStars, polarGrid, sweepMesh, sweepMat;
let innerRingsGroup, innerCoreMesh, wireframeShell, ambientParticles;
let lensFlares = [];
let subsystemNodes = [];
let reactorHoveredEntry = null;
let reactorRaycaster, reactorMouseVec;
let reactorRipples = [];
let reactorBeams = [];
let reactorEqBars = [];
let reactorStageEl, reactorContainerEl;
let hudCursorEl;
let reactorMouseDownPos = null;
let reactorHintEl, reactorHintDimmed = false;
let reactorContainerEls = { labels: null };
let reactorInitAttempts = 0;
let lastEffectiveState = 'idle';
const _tmpColor = new THREE.Color();

let cursorTargetX = -100, cursorTargetY = -100;
let cursorX = -100, cursorY = -100;

const SUBSYSTEMS = [
  { id: 'voice',    color: 0x00ff88, name: 'VOICE',    radius: 2.35, speed: 0.0075, tilt: 0.15, phase: 0.0 },
  { id: 'calendar', color: 0x00e5ff, name: 'CALENDAR', radius: 2.60, speed: -0.0055, tilt: 0.35, phase: 1.1 },
  { id: 'music',    color: 0x8b5cf6, name: 'MUSIC',    radius: 2.30, speed: 0.0090, tilt: 0.50, phase: 2.2 },
  { id: 'status',   color: 0xff8c00, name: 'STATUS',   radius: 2.75, speed: -0.0065, tilt: 0.20, phase: 3.3 },
  { id: 'chat',     color: 0x5ee7f2, name: 'CHAT',     radius: 2.50, speed: 0.0080, tilt: 0.60, phase: 4.4 },
  { id: 'stop',     color: 0xff2b4a, name: 'STOP',     radius: 2.85, speed: -0.0050, tilt: 0.10, phase: 5.5 },
  { id: 'settings', color: 0xffffff, name: 'SETTINGS', radius: 2.20, speed: 0.0105, tilt: 0.42, phase: 6.6 },
  { id: 'comms',    color: 0xffe900, name: 'COMMS',    radius: 2.65, speed: -0.0070, tilt: 0.28, phase: 7.7 }
];

const PLASMA_VERT = `varying vec3 vPosition;varying vec3 vNormal;void main(){vPosition=position;vNormal=normal;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`;
const PLASMA_FRAG = `
uniform float uTime;uniform vec3 uColorA;uniform vec3 uColorB;varying vec3 vPosition;varying vec3 vNormal;
float hash(vec3 p){p=fract(p*0.3183099+vec3(0.1,0.2,0.3));p*=17.0;return fract(p.x*p.y*p.z*(p.x+p.y+p.z));}
float noise(vec3 x){vec3 i=floor(x);vec3 f=fract(x);f=f*f*(3.0-2.0*f);
return mix(mix(mix(hash(i),hash(i+vec3(1,0,0)),f.x),mix(hash(i+vec3(0,1,0)),hash(i+vec3(1,1,0)),f.x),f.y),
mix(mix(hash(i+vec3(0,0,1)),hash(i+vec3(1,0,1)),f.x),mix(hash(i+vec3(0,1,1)),hash(i+vec3(1,1,1)),f.x),f.y),f.z);}
float fbm(vec3 p){float v=0.0;float a=0.5;for(int i=0;i<3;i++){v+=a*noise(p);p*=2.02;a*=0.5;}return v;}
void main(){vec3 p=vPosition*3.0;float t=uTime*0.18;float n1=fbm(p+vec3(t,t*0.5,0.0));float n2=fbm(p*1.5+vec3(-t*0.7,t,t*0.3));float n=(n1+n2)*0.5;
vec3 col=mix(uColorA,uColorB,smoothstep(0.2,0.9,n));vec3 viewDir=vec3(0.0,0.0,1.0);
float fresnel=pow(1.0-abs(dot(vNormal,viewDir)),1.5);col+=uColorB*fresnel*0.8;col*=1.0+n*0.5;gl_FragColor=vec4(col,0.92);}
`;

window.initReactor = function() {
  const canvasEl = document.getElementById('three-reactor');
  const container = document.getElementById('reactorStage');
  if (!canvasEl || !container) return;
  
  if (container.clientWidth < 10 || container.clientHeight < 10) {
    reactorInitAttempts++;
    if (reactorInitAttempts < 100) setTimeout(window.initReactor, 100);
    return;
  }
  
  reactorStageEl = container; reactorContainerEl = canvasEl;
  hudCursorEl = document.getElementById('hud-cursor'); reactorHintEl = document.getElementById('reactorHint');
  reactorContainerEls.labels = document.getElementById('nodeLabels');

  if (renderer) {
    renderer.setSize(container.clientWidth, container.clientHeight);
    camera.aspect = container.clientWidth / container.clientHeight;
    camera.updateProjectionMatrix();
    if (composer) composer.setSize(container.clientWidth, container.clientHeight);
    return;
  }
  
  scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x000610, 0.045);
  camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 1000);
  camera.position.set(0, 1.8, 7);

  try { renderer = new THREE.WebGLRenderer({ canvas: canvasEl, alpha: true, antialias: true }); }
  catch (err) { console.error('[REACTOR] WebGL indisponível:', err); return; }
  renderer.setSize(container.clientWidth, container.clientHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000610, 1);

  composer = null; bloomPass = null;
  try {
    if (typeof THREE.EffectComposer !== 'undefined' && typeof THREE.UnrealBloomPass !== 'undefined') {
      composer = new THREE.EffectComposer(renderer);
      composer.addPass(new THREE.RenderPass(scene, camera));
      bloomPass = new THREE.UnrealBloomPass(new THREE.Vector2(container.clientWidth, container.clientHeight), 0.85, 0.6, 0.85);
      composer.addPass(bloomPass);
      composer.setSize(container.clientWidth, container.clientHeight);
    }
  } catch (err) { composer = null; bloomPass = null; }

  controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.4;
  controls.rotateSpeed = 1.2;
  controls.zoomSpeed = 1.2;
  controls.enablePan = false;
  controls.minDistance = 3.5;
  controls.maxDistance = 11;
  controls.maxPolarAngle = Math.PI / 1.7;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.5;

  let autoRotateResume;
  controls.addEventListener('start', () => { controls.autoRotate = false; clearTimeout(autoRotateResume); });
  controls.addEventListener('end', () => { autoRotateResume = setTimeout(() => { controls.autoRotate = true; }, 1500); });

  polarGrid = new THREE.PolarGridHelper(5, 16, 8, 64, 0x00e5ff, 0x003344);
  polarGrid.position.y = -1.6; polarGrid.material.transparent = true; polarGrid.material.opacity = 0.18;
  scene.add(polarGrid);

  const starsGeo = new THREE.BufferGeometry();
  const starPositions = new Float32Array(800 * 3);
  for (let i = 0; i < starPositions.length; i += 3) {
    starPositions[i] = (Math.random()-0.5)*50; starPositions[i+1] = (Math.random()-0.5)*50; starPositions[i+2] = (Math.random()-0.5)*50;
  }
  starsGeo.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
  reactorStars = new THREE.Points(starsGeo, new THREE.PointsMaterial({ color: 0x5ee7f2, size: 0.07, transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending }));
  scene.add(reactorStars);

  reactorGroup = new THREE.Group(); scene.add(reactorGroup);

  function arcSegment(inner, outer, thetaStart, thetaLength, color, opacity, segs = 64) {
    const geo = new THREE.RingGeometry(inner, outer, segs, 1, thetaStart, thetaLength);
    const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity, side: THREE.DoubleSide, blending: THREE.AdditiveBlending });
    return new THREE.Mesh(geo, mat);
  }

  innerCoreMesh = new THREE.Mesh(
    new THREE.SphereGeometry(0.28, 24, 24),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9 })
  );
  reactorGroup.add(innerCoreMesh);

  coreMat = new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 }, uColorA: { value: new THREE.Color(0x001a22) }, uColorB: { value: new THREE.Color(0x00e5ff) } },
    vertexShader: PLASMA_VERT, fragmentShader: PLASMA_FRAG, transparent: true
  });
  coreMesh = new THREE.Mesh(new THREE.SphereGeometry(0.55, 64, 64), coreMat);
  reactorGroup.add(coreMesh);

  wireframeShell = new THREE.Mesh(
    new THREE.IcosahedronGeometry(0.68, 1),
    new THREE.MeshBasicMaterial({ color: 0x00e5ff, wireframe: true, transparent: true, opacity: 0.12, blending: THREE.AdditiveBlending })
  );
  reactorGroup.add(wireframeShell);

  haloMesh = new THREE.Mesh(new THREE.SphereGeometry(0.78, 32, 32), new THREE.MeshBasicMaterial({ color: 0x00e5ff, transparent: true, opacity: 0.1, blending: THREE.AdditiveBlending }));
  reactorGroup.add(haloMesh);

  innerRingsGroup = new THREE.Group();
  reactorGroup.add(innerRingsGroup);

  for (let i = 0; i < 24; i++) {
    const seg = (Math.PI*2)/24;
    innerRingsGroup.add(arcSegment(0.70, 0.72, i*seg + seg*0.15, seg*0.55, 0x5ee7f2, 0.4));
  }
  for (let i = 0; i < 32; i++) {
    const seg = (Math.PI*2)/32;
    innerRingsGroup.add(arcSegment(0.83, 0.85, i*seg, seg*0.25, 0x00e5ff, 0.45));
  }
  innerRingsGroup.add(arcSegment(0.955, 0.965, 0, Math.PI * 2, 0xffffff, 0.25, 128));

  reactorGroup.add(arcSegment(0.63, 0.65, 0, Math.PI * 2, 0x00e5ff, 0.4, 128));
  for (let i = 0; i < 8; i++) { const seg = (Math.PI*2)/8; reactorGroup.add(arcSegment(0.92, 1.05, i*seg, seg*0.9, 0x00e5ff, 0.55)); }
  reactorGroup.add(arcSegment(1.13, 1.16, 0, Math.PI * 2, 0x00e5ff, 0.25, 128));
  for (let i = 0; i < 4; i++) { const seg = (Math.PI*2)/4; reactorGroup.add(arcSegment(1.22, 1.32, i*seg + seg*0.15, seg*0.45, 0xff2b4a, 0.65)); }
  for (let i = 0; i < 60; i++) {
    const angle = (Math.PI*2*i)/60; const segLen = (Math.PI*2)/60 * 0.4;
    reactorGroup.add(arcSegment(1.38, 1.42, angle, segLen, 0x00e5ff, i % 5 === 0 ? 0.65 : 0.3));
  }
  reactorGroup.add(arcSegment(1.58, 1.60, 0, Math.PI * 2, 0x5ee7f2, 0.15, 128));
  for (let i = 0; i < 48; i++) { const seg = (Math.PI*2)/48; reactorGroup.add(arcSegment(1.73, 1.76, i*seg, seg*0.55, 0x00e5ff, 0.35)); }

  for (let i = 0; i < 8; i++) {
    const angle = (Math.PI * 2 * i) / 8;
    const spokeGeo = new THREE.PlaneGeometry(0.5, 0.006);
    const spokeMat = new THREE.MeshBasicMaterial({ color: 0x5ee7f2, transparent: true, opacity: 0.28, blending: THREE.AdditiveBlending, side: THREE.DoubleSide });
    const spoke = new THREE.Mesh(spokeGeo, spokeMat);
    spoke.position.set(Math.cos(angle) * 1.0, Math.sin(angle) * 1.0, 0);
    spoke.rotation.z = angle;
    reactorGroup.add(spoke);
  }

  for (let i = 0; i < 4; i++) {
    const angle = (Math.PI * 2 * i) / 4;
    const flare = new THREE.Mesh(
      new THREE.SphereGeometry(0.03, 8, 8),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9 })
    );
    flare.position.set(Math.cos(angle) * 1.9, Math.sin(angle) * 1.9, 0);
    flare.userData.baseAngle = angle;
    reactorGroup.add(flare);
    lensFlares.push(flare);
  }

  sweepMat = new THREE.MeshBasicMaterial({ color: 0x00e5ff, transparent: true, opacity: 0.09, side: THREE.DoubleSide, blending: THREE.AdditiveBlending });
  sweepMesh = new THREE.Mesh(new THREE.RingGeometry(1.85, 2.05, 64, 1, 0, Math.PI / 3), sweepMat);
  sweepMesh.rotation.x = -Math.PI / 2; sweepMesh.position.y = 0.02;
  reactorGroup.add(sweepMesh);

  const particleCount = 100;
  const pGeo = new THREE.BufferGeometry();
  const pPos = new Float32Array(particleCount * 3);
  const pData = [];
  for (let i = 0; i < particleCount; i++) {
    const r = 1.85 + Math.random() * 0.25; const a = Math.random() * Math.PI * 2;
    pData.push({ r, a, speed: 0.0008 + Math.random() * 0.002 });
    pPos[i*3] = Math.cos(a) * r; pPos[i*3+1] = Math.sin(a) * r; pPos[i*3+2] = (Math.random() - 0.5) * 0.2;
  }
  pGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3));
  orbitalParticles = new THREE.Points(pGeo, new THREE.PointsMaterial({ color: 0x5ee7f2, size: 0.04, transparent: true, opacity: 0.75, blending: THREE.AdditiveBlending }));
  reactorGroup.add(orbitalParticles);

  const ambGeo = new THREE.BufferGeometry();
  const ambCount = 80;
  const ambPositions = new Float32Array(ambCount * 3);
  for (let i = 0; i < ambCount; i++) {
    const r = 2.5 + Math.random() * 3;
    const theta = Math.random() * Math.PI * 2;
    const phi = (Math.random() - 0.5) * Math.PI * 0.8;
    ambPositions[i*3] = r * Math.cos(theta) * Math.cos(phi);
    ambPositions[i*3+1] = r * Math.sin(phi);
    ambPositions[i*3+2] = r * Math.sin(theta) * Math.cos(phi);
  }
  ambGeo.setAttribute('position', new THREE.BufferAttribute(ambPositions, 3));
  ambientParticles = new THREE.Points(ambGeo, new THREE.PointsMaterial({ color: 0x5ee7f2, size: 0.04, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending }));
  scene.add(ambientParticles);

  SUBSYSTEMS.forEach((cfg) => {
    const group = new THREE.Group(); reactorGroup.add(group);
    const sphere = new THREE.Mesh(new THREE.SphereGeometry(0.085, 16, 16), new THREE.MeshBasicMaterial({ color: cfg.color }));
    group.add(sphere);
    const halo2 = new THREE.Mesh(new THREE.SphereGeometry(0.18, 16, 16), new THREE.MeshBasicMaterial({ color: cfg.color, transparent: true, opacity: 0.25, blending: THREE.AdditiveBlending }));
    group.add(halo2);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.19, 0.004, 6, 32), new THREE.MeshBasicMaterial({ color: cfg.color, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending }));
    ring.rotation.x = Math.PI / 2; group.add(ring);
    const orbitHalo1 = new THREE.Mesh(new THREE.TorusGeometry(0.28, 0.006, 6, 40), new THREE.MeshBasicMaterial({ color: cfg.color, transparent: true, opacity: 0, blending: THREE.AdditiveBlending }));
    group.add(orbitHalo1);
    const orbitHalo2 = new THREE.Mesh(new THREE.TorusGeometry(0.36, 0.004, 6, 48), new THREE.MeshBasicMaterial({ color: cfg.color, transparent: true, opacity: 0, blending: THREE.AdditiveBlending }));
    group.add(orbitHalo2);
    const labelEl = document.createElement('div');
    labelEl.className = 'node-label';
    labelEl.textContent = '// ' + cfg.name;
    labelEl.style.color = '#' + cfg.color.toString(16).padStart(6, '0');
    if (reactorContainerEls.labels) reactorContainerEls.labels.appendChild(labelEl);
    const entry = { group, sphere, halo: halo2, ring, orbitHalo1, orbitHalo2, data: cfg, labelEl, angle: cfg.phase, wobble: Math.random() * Math.PI * 2 };
    sphere.userData.entry = entry;
    subsystemNodes.push(entry);
  });

  const eqCount = 32, eqRadius = 2.05;
  for (let i = 0; i < eqCount; i++) {
    const a = (i / eqCount) * Math.PI * 2;
    const bar = new THREE.Mesh(new THREE.BoxGeometry(0.035, 1, 0.035), new THREE.MeshBasicMaterial({ color: 0x8b5cf6, transparent: true, opacity: 0, blending: THREE.AdditiveBlending }));
    bar.position.set(Math.cos(a) * eqRadius, 0, Math.sin(a) * eqRadius);
    bar.scale.y = 0.01;
    scene.add(bar); reactorEqBars.push(bar);
  }

  reactorRaycaster = new THREE.Raycaster();
  reactorMouseVec = new THREE.Vector2();

  const stage = reactorStageEl;
  stage.addEventListener('pointerenter', () => { if (hudCursorEl) hudCursorEl.classList.add('visible'); });
  stage.addEventListener('pointerleave', () => {
    if (hudCursorEl) hudCursorEl.classList.remove('visible', 'hover');
    if (reactorHoveredEntry) {
      reactorHoveredEntry.labelEl?.classList.remove('visible');
      reactorHoveredEntry.sphere.scale.setScalar(1);
      reactorHoveredEntry = null;
    }
  });
  stage.addEventListener('pointermove', (e) => {
    const rect = stage.getBoundingClientRect();
    reactorMouseVec.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    reactorMouseVec.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    cursorTargetX = e.clientX; cursorTargetY = e.clientY;
  });
  stage.addEventListener('pointerdown', (e) => {
    reactorMouseDownPos = { x: e.clientX, y: e.clientY };
    const rect = stage.getBoundingClientRect();
    reactorMouseVec.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    reactorMouseVec.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
  });
  stage.addEventListener('pointerup', (e) => {
    if (!reactorMouseDownPos) return;
    const dx = Math.abs(e.clientX - reactorMouseDownPos.x);
    const dy = Math.abs(e.clientY - reactorMouseDownPos.y);
    reactorMouseDownPos = null;
    if (dx < 6 && dy < 6) handleReactorClick();
  });

  const tempVec = new THREE.Vector3();
  let t = 0;

  function animateReactor() {
    requestAnimationFrame(animateReactor);
    t += 0.016;
    if (controls) controls.update();
    coreMat.uniforms.uTime.value = t;

    let effectiveState = 'idle';
    if (window.listening) effectiveState = 'listening';
    else if (window.speaking) effectiveState = 'speaking';
    else if (window.fridayState === 'thinking') effectiveState = 'thinking';
    else if (window.fridayState === 'tooling') effectiveState = 'tooling';
    else if (window.fridayState === 'error') effectiveState = 'error';

    if (effectiveState !== lastEffectiveState) {
      lastEffectiveState = effectiveState;
      document.body.dataset.state = effectiveState;
      if (window.redrawSparklines) window.redrawSparklines();
    }

    const palette = STATE_PALETTE[effectiveState];
    _tmpColor.setHex(palette.primary);
    coreMat.uniforms.uColorB.value.lerp(_tmpColor, 0.05);
    haloMesh.material.color.lerp(_tmpColor, 0.05);
    sweepMat.color.lerp(_tmpColor, 0.05);
    orbitalParticles.material.color.lerp(_tmpColor, 0.05);
    reactorStars.material.color.lerp(_tmpColor, 0.05);
    wireframeShell.material.color.lerp(_tmpColor, 0.05);
    if (bloomPass) {
      bloomPass.strength += (palette.bloomStrength - bloomPass.strength) * 0.05;
      bloomPass.threshold += (palette.bloomThreshold - bloomPass.threshold) * 0.05;
    }

    const pulse = 1 + Math.sin(t * 2.5) * 0.08;
    coreMesh.scale.setScalar(pulse);
    haloMesh.scale.setScalar(pulse);

    const pAttr = orbitalParticles.geometry.attributes.position;
    const pArr = pAttr.array;
    for (let i = 0; i < particleCount; i++) {
      const d = pData[i]; d.a += d.speed;
      pArr[i*3] = Math.cos(d.a) * d.r;
      pArr[i*3+1] = Math.sin(d.a) * d.r;
    }
    pAttr.needsUpdate = true;

    if (window.audioAnalyser && window.speaking) {
      const dataArr = new Uint8Array(window.audioAnalyser.frequencyBinCount);
      window.audioAnalyser.getByteFrequencyData(dataArr);
      let sum = 0; for (let i = 0; i < 8; i++) sum += dataArr[i];
      coreMesh.scale.setScalar(1 + (sum / 8 / 255) * 0.4);

      const eqData = new Uint8Array(window.audioAnalyser.frequencyBinCount);
      window.audioAnalyser.getByteFrequencyData(eqData);
      reactorEqBars.forEach((bar, i) => {
        const idx = Math.floor((i / reactorEqBars.length) * eqData.length);
        const val = eqData[idx] / 255;
        const h = 0.06 + val * 0.9;
        bar.scale.y = h; bar.position.y = h / 2;
        bar.material.opacity = 0.35 + val * 0.65;
      });
    } else {
      reactorEqBars.forEach(bar => {
        bar.scale.y += (0.01 - bar.scale.y) * 0.15;
        bar.position.y = bar.scale.y / 2;
        bar.material.opacity *= 0.9;
      });
    }

    sweepMesh.rotation.z += 0.008;
    innerRingsGroup.rotation.z -= 0.004;
    wireframeShell.rotation.x += 0.003;
    wireframeShell.rotation.y += 0.005;

    lensFlares.forEach((flare, i) => {
      const p = 0.6 + Math.sin(t * 2 + i * 1.5) * 0.4;
      flare.material.opacity = p;
      flare.scale.setScalar(0.8 + p * 0.5);
      const driftAngle = flare.userData.baseAngle + Math.sin(t * 0.3 + i) * 0.05;
      flare.position.x = Math.cos(driftAngle) * 1.9;
      flare.position.y = Math.sin(driftAngle) * 1.9;
    });

    ambientParticles.rotation.y += 0.0008;
    ambientParticles.rotation.x += 0.0003;

    subsystemNodes.forEach((entry, i) => {
      entry.angle += entry.data.speed;
      const a = entry.angle, r = entry.data.radius, tilt = entry.data.tilt;
      const wobble = Math.sin(t * 1.2 + entry.wobble) * 0.15;
      entry.group.position.set(Math.cos(a) * r, Math.sin(a) * r * Math.sin(tilt) + wobble, Math.sin(a) * r * Math.cos(tilt));
      entry.ring.rotation.z += 0.02; entry.ring.rotation.y += 0.015;
      const hScale = 1 + Math.sin(t * 3 + i) * 0.15;
      entry.halo.scale.setScalar(hScale);
      const isHovered = reactorHoveredEntry === entry;
      entry.orbitHalo1.material.opacity += ((isHovered ? 0.75 : 0) - entry.orbitHalo1.material.opacity) * 0.15;
      entry.orbitHalo2.material.opacity += ((isHovered ? 0.45 : 0) - entry.orbitHalo2.material.opacity) * 0.15;
      if (isHovered) {
        const hs = 1.35 + Math.sin(t * 8) * 0.1;
        entry.sphere.scale.setScalar(hs);
        entry.halo.scale.setScalar(hs * 1.4);
        entry.orbitHalo1.rotation.x += 0.02; entry.orbitHalo1.rotation.y += 0.015;
        entry.orbitHalo2.rotation.x -= 0.015; entry.orbitHalo2.rotation.y -= 0.02;
      } else entry.sphere.scale.setScalar(1);
      if (entry.labelEl) {
        entry.group.getWorldPosition(tempVec);
        tempVec.project(camera);
        if (tempVec.z > 1) entry.labelEl.style.display = 'none';
        else {
          entry.labelEl.style.display = '';
          const lx = (tempVec.x * 0.5 + 0.5) * canvasEl.clientWidth;
          const ly = (-tempVec.y * 0.5 + 0.5) * canvasEl.clientHeight;
          entry.labelEl.style.transform = `translate(${lx}px, ${ly}px) translate(-50%, calc(-100% - 16px))`;
        }
      }
    });

    if (reactorRaycaster) {
      reactorRaycaster.setFromCamera(reactorMouseVec, camera);
      const hits = reactorRaycaster.intersectObjects(subsystemNodes.map(n => n.sphere), false);
      let newHovered = hits.length > 0 ? hits[0].object.userData.entry : null;
      if (newHovered !== reactorHoveredEntry) {
        if (reactorHoveredEntry) reactorHoveredEntry.labelEl?.classList.remove('visible');
        reactorHoveredEntry = newHovered;
        if (reactorHoveredEntry) {
          reactorHoveredEntry.labelEl?.classList.add('visible');
          try { if (window.playTone) window.playTone(1200, 'sine', 0.035, 0.03); } catch(e) {}
          if (hudCursorEl) hudCursorEl.classList.add('hover');
        } else { if (hudCursorEl) hudCursorEl.classList.remove('hover'); }
      }
      if (!reactorHoveredEntry && hudCursorEl) {
        const coreHits = reactorRaycaster.intersectObject(coreMesh, false);
        if (coreHits.length > 0) hudCursorEl.classList.add('hover');
        else hudCursorEl.classList.remove('hover');
      }
    }

    reactorRipples = reactorRipples.filter(r => {
      r.life += 0.016;
      const p = r.life / r.maxLife;
      if (p >= 1) { scene.remove(r.mesh); r.mesh.geometry.dispose(); r.mesh.material.dispose(); return false; }
      const s = 1 + p * 9; r.mesh.scale.set(s, s, s); r.mesh.material.opacity = 0.85 * (1 - p);
      return true;
    });
    reactorBeams = reactorBeams.filter(b => {
      b.life += 0.016;
      if (b.life >= b.maxLife) { scene.remove(b.line); b.line.geometry.dispose(); b.line.material.dispose(); return false; }
      b.line.material.opacity = 1 - (b.life / b.maxLife);
      return true;
    });

    reactorStars.rotation.y = t * 0.015;
    polarGrid.rotation.y = -t * 0.04;

    if (composer) composer.render();
    else renderer.render(scene, camera);
  }
  animateReactor();
};

function handleReactorClick() {
  if (!reactorRaycaster || !reactorMouseVec) return;
  reactorRaycaster.setFromCamera(reactorMouseVec, camera);
  const nodeHits = reactorRaycaster.intersectObjects(subsystemNodes.map(n => n.sphere), false);
  if (nodeHits.length > 0) { activateNode(nodeHits[0].object.userData.entry); window.dimReactorHint(); return; }
  const coreHits = reactorRaycaster.intersectObject(coreMesh, false);
  if (coreHits.length > 0) {
    if (!window.listening && !window.speaking) { if (window.startVoice) window.startVoice(false); }
    else { if (window.stopVoice) window.stopVoice(); }
    spawnRipple(new THREE.Vector3(0, -1.6, 0), 0x00e5ff);
    try { if (window.playTone) window.playTone(660, 'sine', 0.08, 0.05); } catch(e) {}
    window.dimReactorHint(); return;
  }
  const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 1.6);
  const hit = new THREE.Vector3();
  reactorRaycaster.ray.intersectPlane(plane, hit);
  if (hit && Math.abs(hit.x) < 8 && Math.abs(hit.z) < 8) {
    spawnRipple(hit, 0x00e5ff);
    try { if (window.playTone) window.playTone(400, 'sine', 0.05, 0.03); } catch(e) {}
  }
  window.dimReactorHint();
}

function activateNode(entry) {
  if (!entry) return;
  const data = entry.data;
  const worldPos = new THREE.Vector3();
  entry.group.getWorldPosition(worldPos);
  spawnBeam(worldPos.clone(), data.color);
  spawnRipple(new THREE.Vector3(worldPos.x, -1.6, worldPos.z), data.color);
  try { if (window.playTone) window.playTone(800, 'sine', 0.09, 0.05); } catch(e) {}
  
  switch (data.id) {
    case 'voice': 
      if (!window.listening) { if (window.startVoice) window.startVoice(false); } 
      else { if (window.stopVoice) window.stopVoice(); } 
      break;
    case 'calendar': 
      if (window.loadCalendarEvents) {
        document.getElementById('calendarPanel').classList.remove('hidden'); 
        window.loadCalendarEvents('upcoming'); 
        if (window.setStatus) window.setStatus('Sincronizando agenda...'); 
      }
      break;
    case 'music': {
      const container = document.getElementById('musicPlayerContainer');
      if (container && !container.classList.contains('hidden')) {
        container.classList.add('hidden');
        if (window.ytPlayer && window.ytPlayer.pauseVideo) window.ytPlayer.pauseVideo();
        if (window.setStatus) window.setStatus('Música pausada.');
      } else if (container) {
        container.classList.remove('hidden');
        if (window.ytPlayer && window.ytPlayer.playVideo) window.ytPlayer.playVideo();
        if (window.setStatus) window.setStatus('Música retomada.');
      } else { 
        if (window.setStatus) window.setStatus('Nenhuma música carregada.'); 
        if (window.addMessage) window.addMessage('FRIDAY', '🎵 Nenhuma faixa carregada. Peça uma música no chat.'); 
      }
      break;
    }
    case 'status': {
      const el = document.getElementById('status');
      el.style.transition = 'all 0.3s';
      el.style.background = 'rgba(0,229,255,0.15)';
      setTimeout(() => { el.style.background = ''; }, 900);
      if (window.setStatus) window.setStatus('Diagnóstico: 8 subsistemas operacionais.');
      if (window.showToast) window.showToast('DIAGNÓSTICO · 8 subsistemas online');
      break;
    }
    case 'chat': 
      document.getElementById('command').focus(); 
      if (window.setStatus) window.setStatus('Canal de comando ativo.'); 
      break;
    case 'stop': 
      if (window.stopVoice) window.stopVoice(); 
      if (window.setStatus) window.setStatus('Processos de áudio interrompidos.'); 
      break;
    case 'settings': 
      document.getElementById('settingsModal').classList.remove('hidden'); 
      break;
    case 'comms': {
      const msgs = ['📡 Varredura completa. 8 subsistemas online.','🧠 Núcleo neural estável. Latência 12ms.','🛰 Link AES-256 confirmado.','⚡ Célula em carga máxima.','📊 Telemetria nominal.'];
      const m = msgs[Math.floor(Math.random() * msgs.length)];
      if (window.addMessage) window.addMessage('FRIDAY', m); 
      if (window.speakText) window.speakText(m, false); 
      if (window.showToast) window.showToast(m);
      break;
    }
  }
}

function spawnRipple(pos, color) {
  const geo = new THREE.RingGeometry(0.06, 0.14, 48);
  const mat = new THREE.MeshBasicMaterial({ color: color || 0x00e5ff, transparent: true, opacity: 0.9, side: THREE.DoubleSide, blending: THREE.AdditiveBlending });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.rotation.x = -Math.PI / 2; mesh.position.copy(pos);
  scene.add(mesh);
  reactorRipples.push({ mesh, life: 0, maxLife: 0.9 });
}

function spawnBeam(targetPos, color) {
  const points = [new THREE.Vector3(0, 0, 0), targetPos.clone()];
  const geo = new THREE.BufferGeometry().setFromPoints(points);
  const mat = new THREE.LineBasicMaterial({ color: color || 0x00e5ff, transparent: true, opacity: 1, blending: THREE.AdditiveBlending });
  const line = new THREE.Line(geo, mat);
  scene.add(line);
  reactorBeams.push({ line, life: 0, maxLife: 0.55 });
}

window.dimReactorHint = function() { 
  if (!reactorHintDimmed && reactorHintEl) { 
    reactorHintDimmed = true; 
    reactorHintEl.classList.add('dimmed'); 
  } 
};

function animateHudCursor() {
  requestAnimationFrame(animateHudCursor);
  cursorX += (cursorTargetX - cursorX) * 0.5;
  cursorY += (cursorTargetY - cursorY) * 0.5;
  if (hudCursorEl) hudCursorEl.style.transform = `translate(${cursorX}px, ${cursorY}px) translate(-50%, -50%)`;
}
animateHudCursor();

window.addEventListener('resize', () => {
  if (renderer && reactorStageEl) {
    const c = reactorStageEl;
    renderer.setSize(c.clientWidth, c.clientHeight);
    camera.aspect = c.clientWidth / c.clientHeight;
    camera.updateProjectionMatrix();
    if (composer) composer.setSize(c.clientWidth, c.clientHeight);
  }
});

window.setupAudioAnalyser = function(audioElement) {
  if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
  if (audioContext.state === 'suspended') audioContext.resume();
  try {
    const source = audioContext.createMediaElementSource(audioElement);
    window.audioAnalyser = audioContext.createAnalyser();
    window.audioAnalyser.fftSize = 256;
    source.connect(window.audioAnalyser);
    window.audioAnalyser.connect(audioContext.destination);
  } catch(e) {}
};
