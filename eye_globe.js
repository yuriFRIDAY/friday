/* ============================================================
   FRIDAY — Olho de Deus (Globo 3D e Dados Reais)
   ============================================================ */

let eyeScene, eyeCamera, eyeRenderer, earthMesh, pointsGroup;

function latLngToVector3(lat, lng, radius = 100) {
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = (lng + 180) * (Math.PI / 180);
  const x = -(radius * Math.sin(phi) * Math.cos(theta));
  const z = (radius * Math.sin(phi) * Math.sin(theta));
  const y = (radius * Math.cos(phi));
  return new THREE.Vector3(x, y, z);
}

window.initEyeOfGodGlobe = function() {
  const canvas = document.getElementById('eye-canvas');
  if (!canvas) return;
  if (eyeRenderer) return;

  eyeScene = new THREE.Scene();
  setTimeout(() => {
    const w = window.innerWidth;
    const h = window.innerHeight;
    eyeCamera = new THREE.PerspectiveCamera(45, w / h, 0.1, 1000);
    eyeCamera.position.z = 250;

    eyeRenderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
    eyeRenderer.setSize(w, h);
    eyeRenderer.setPixelRatio(window.devicePixelRatio);

    const geometry = new THREE.SphereGeometry(100, 64, 64);
    const wireframeMat = new THREE.MeshBasicMaterial({
      color: 0x00e5ff,
      wireframe: true,
      transparent: true,
      opacity: 0.15
    });
    earthMesh = new THREE.Mesh(geometry, wireframeMat);
    eyeScene.add(earthMesh);

    const textureLoader = new THREE.TextureLoader();
    textureLoader.crossOrigin = 'anonymous';
    textureLoader.load(
      'https://unpkg.com/three-globe/example/img/earth-night.jpg',
      (texture) => {
        const material = new THREE.MeshPhongMaterial({
          map: texture,
          bumpScale: 0.5,
          specular: new THREE.Color('grey'),
          shininess: 10
        });
        earthMesh.material = material;
        
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
        eyeScene.add(ambientLight);
        const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
        dirLight.position.set(5, 3, 5);
        eyeScene.add(dirLight);
      },
      undefined, 
      (err) => {
        console.warn("[OLHO DE DEUS] Textura falhou, mantendo wireframe.", err);
      }
    );

    pointsGroup = new THREE.Group();
    eyeScene.add(pointsGroup);

    const controls = new THREE.OrbitControls(eyeCamera, eyeRenderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.rotateSpeed = 0.5;
    controls.enableZoom = true;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.5;

    function animateEye() {
      requestAnimationFrame(animateEye);
      controls.update();
      eyeRenderer.render(eyeScene, eyeCamera);
    }
    animateEye();

    fetchEarthquakes();
  }, 100);
};

async function fetchEarthquakes() {
  try {
    const response = await fetch('https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_month.geojson');
    const data = await response.json();

    while(pointsGroup.children.length > 0){ 
      pointsGroup.remove(pointsGroup.children[0]); 
    }

    data.features.forEach(feature => {
      const coords = feature.geometry.coordinates;
      const mag = feature.properties.mag;
      const lng = coords[0];
      const lat = coords[1];

      const pos = latLngToVector3(lat, lng, 101);

      let color = 0x00ffff;
      if (mag >= 5) color = 0xff0000;
      else if (mag >= 4) color = 0xffaa00;

      const pointGeo = new THREE.SphereGeometry(0.5 + (mag * 0.2), 8, 8);
      const pointMat = new THREE.MeshBasicMaterial({ color: color });
      const pointMesh = new THREE.Mesh(pointGeo, pointMat);
      
      pointMesh.position.copy(pos);
      pointsGroup.add(pointMesh);
    });

    console.log(`[OLHO DE DEUS] ${data.features.length} terremotos plotados.`);
  } catch (error) {
    console.error("[OLHO DE DEUS] Falha ao buscar dados do USGS:", error);
  }
}

window.addEventListener('resize', () => {
  if (eyeCamera && eyeRenderer) {
    eyeCamera.aspect = window.innerWidth / window.innerHeight;
    eyeCamera.updateProjectionMatrix();
    eyeRenderer.setSize(window.innerWidth, window.innerHeight);
  }
});
