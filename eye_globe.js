/* ============================================================
   FRIDAY — Olho de Deus (Globo 3D e Dados Reais)
   ============================================================ */

let eyeScene, eyeCamera, eyeRenderer, earthMesh, pointsGroup;

// Função que converte Latitude/Longitude para posição 3D na esfera
function latLngToVector3(lat, lng, radius = 100) {
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = (lng + 180) * (Math.PI / 180);
  const x = -(radius * Math.sin(phi) * Math.cos(theta));
  const z = (radius * Math.sin(phi) * Math.sin(theta));
  const y = (radius * Math.cos(phi));
  return new THREE.Vector3(x, y, z);
}

// Função principal de inicialização
window.initEyeOfGodGlobe = function() {
  const canvas = document.getElementById('eye-canvas');
  if (!canvas) return;

  // Se já foi inicializado, apenas retorna
  if (eyeRenderer) return;

  // 1. Configurar Cena, Câmera e Renderizador
  eyeScene = new THREE.Scene();
  eyeCamera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 1000);
  eyeCamera.position.z = 250;

  eyeRenderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
  eyeRenderer.setSize(window.innerWidth, window.innerHeight);
  eyeRenderer.setPixelRatio(window.devicePixelRatio);

  // 2. Criar a Terra
  const geometry = new THREE.SphereGeometry(100, 64, 64);
  // Textura pública da Terra (mapa noturno)
  const textureLoader = new THREE.TextureLoader();
  textureLoader.crossOrigin = 'anonymous';
  const earthTexture = textureLoader.load('https://unpkg.com/three-globe/example/img/earth-night.jpg');
  
  const material = new THREE.MeshPhongMaterial({
    map: earthTexture,
    bumpScale: 0.5,
    specular: new THREE.Color('grey'),
    shininess: 10
  });

  earthMesh = new THREE.Mesh(geometry, material);
  eyeScene.add(earthMesh);

  // 3. Iluminação (para a textura aparecer)
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
  eyeScene.add(ambientLight);

  const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
  dirLight.position.set(5, 3, 5);
  eyeScene.add(dirLight);

  // 4. Grupo para os pontos de terremotos
  pointsGroup = new THREE.Group();
  eyeScene.add(pointsGroup);

  // 5. Adicionar controles (arrastar para girar)
  const controls = new THREE.OrbitControls(eyeCamera, eyeRenderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;
  controls.rotateSpeed = 0.5;
  controls.enableZoom = true;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.5;

  // 6. Animação
  function animateEye() {
    requestAnimationFrame(animateEye);
    controls.update();
    eyeRenderer.render(eyeScene, eyeCamera);
  }
  animateEye();

  // 7. Carregar os dados reais
  fetchEarthquakes();
};

// Função que busca os dados do USGS e plota
async function fetchEarthquakes() {
  try {
    // API do USGS: Terremotos de magnitude 2.5+ nos últimos 30 dias
    const response = await fetch('https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_month.geojson');
    const data = await response.json();

    // Limpar pontos antigos, se houver
    while(pointsGroup.children.length > 0){ 
      pointsGroup.remove(pointsGroup.children[0]); 
    }

    // Atualizar o contador na interface (opcional)
    const countEl = document.getElementById('fire-count'); // Reutilizando o id para exemplo
    if (countEl) countEl.textContent = data.features.length;

    data.features.forEach(feature => {
      const coords = feature.geometry.coordinates; // [longitude, latitude, depth]
      const mag = feature.properties.mag;
      
      const lng = coords[0];
      const lat = coords[1];

      // Converter para posição 3D (um pouco acima da superfície)
      const pos = latLngToVector3(lat, lng, 100.5);

      // Cor baseada na magnitude
      let color = 0x00ffff; // Ciano (Fraco)
      if (mag >= 5) color = 0xff0000; // Vermelho (Forte)
      else if (mag >= 4) color = 0xffaa00; // Laranja (Médio)

      // Criar um pequeno ponto brilhante
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

// Função de redimensionamento
window.addEventListener('resize', () => {
  if (eyeCamera && eyeRenderer) {
    eyeCamera.aspect = window.innerWidth / window.innerHeight;
    eyeCamera.updateProjectionMatrix();
    eyeRenderer.setSize(window.innerWidth, window.innerHeight);
  }
});
