class ViewerRenderer {
    constructor(container) {
        this.container = container;
        this.scene = new THREE.Scene();
        this.camera = null;
        this.renderer = null;
        this.controls = null;
        this.lights = [];
    }

    initialize() {
        this._setupCamera();
        this._setupRenderer();
        this._setupControls();
        this._setupLighting();
        this._setupGrid();
        this._appendToContainer();
        this.render();
    }

    _setupCamera() {
        this.camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 2000);
        this.camera.position.set(0, 0, 150);
    }

    _setupRenderer() {
        this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        this.renderer.setSize(window.innerWidth, window.innerHeight);
        this.renderer.setPixelRatio(window.devicePixelRatio);
        this.renderer.setClearColor(0x202020, 1); // Darker background
        this.renderer.outputEncoding = THREE.sRGBEncoding;
        this.renderer.shadowMap.enabled = true;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    }

    _setupControls() {
        this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.1;
        this.controls.screenSpacePanning = true;
    }

    _setupLighting() {
        // Adaptive directional light
        const directionalLight = new THREE.DirectionalLight(0xffffff, 3);
        directionalLight.position.set(50, 50, 100);
        directionalLight.castShadow = true;
        this.scene.add(directionalLight);

        // Back light
        const backLight = new THREE.DirectionalLight(0xffffff, 2.5);
        backLight.position.set(-50, -50, -100);
        this.scene.add(backLight);

        // Ambient light for even illumination
        const ambientLight = new THREE.AmbientLight(0x404040, 2);
        this.scene.add(ambientLight);

        this.lights = [directionalLight, backLight, ambientLight];
    }

    _setupGrid() {
        const gridHelper = new THREE.GridHelper(200, 20);
        this.scene.add(gridHelper);
    }

    _appendToContainer() {
        this.container.appendChild(this.renderer.domElement);
    }

    updateCamera(boundingBox) {
        const center = boundingBox.getCenter(new THREE.Vector3());
        const size = boundingBox.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);
        const distance = maxDim / (2 * Math.tan((this.camera.fov * Math.PI) / 360));

        this.camera.position.set(center.x, center.y, distance * 2.5);
        this.camera.lookAt(center);
        this.camera.near = 0.1;
        this.camera.far = distance * 4;
        this.camera.updateProjectionMatrix();
    }

    render() {
        requestAnimationFrame(() => this.render());
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
    }
}

export default ViewerRenderer;
