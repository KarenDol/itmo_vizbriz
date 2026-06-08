class ViewerRenderer {
    constructor(container, cameraConfig = {}) {
        this.container = container;
        this.scene = new THREE.Scene();
        this.camera = null;
        this.renderer = null;
        this.controls = null;
        this.lights = [];

        // Default camera configuration
        this.cameraConfig = {
            fov: 45,  // Reduce from 75 to 45 for better initial view
            near: 0.1,  // Adjust from 1 to 0.1 for closer objects
            far: 2000,  // Increase from 2 to 1000 for better depth range
            position: { x: 0, y: 0, z: 600 }
        };
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

    initializeScene() {
        this._setupLighting();
        this._setupGrid();
    }

    _setupCamera() {
        this.camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 2000);
        this.camera.position.set(0, 0, 600); // Default position with a reasonable distance
    }

    _setupRenderer() {
        this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        this.renderer.setSize(window.innerWidth, window.innerHeight);
        this.renderer.setPixelRatio(window.devicePixelRatio);
        this.renderer.setClearColor(0x000000, 1);
        this.renderer.outputEncoding = THREE.sRGBEncoding;
        this.renderer.shadowMap.enabled = false;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    }

    _setupControls() {
        this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.2;
        this.controls.screenSpacePanning = true;

    // Rotation speed (lower = slower rotation)
    this.controls.rotateSpeed = 0.5;
     // Zoom speed (lower = slower zoom)
     this.controls.zoomSpeed = 0.3;

     // Pan settings (if you want slower panning or full screen-space panning):
     this.controls.screenSpacePanning = true;
     
    }

    _setupLighting() {
        const distance = Math.sqrt(300 ** 2 + 300 ** 2 + 300 ** 2);
    
        const directionalLight1 = new THREE.DirectionalLight(0xffffff, 1.5);
        directionalLight1.position.set(distance, distance, distance);
        directionalLight1.castShadow = true;
        this.scene.add(directionalLight1);
    
        const directionalLight2 = new THREE.DirectionalLight(0xffffff, 1.2);
        directionalLight2.position.set(-distance, -distance, -distance);
        this.scene.add(directionalLight2);
    
        const directionalLight3 = new THREE.DirectionalLight(0xffffff, 1.2);
        directionalLight3.position.set(distance, -distance, distance);
        this.scene.add(directionalLight3);
    
        const directionalLight4 = new THREE.DirectionalLight(0xffffff, 1.2);
        directionalLight4.position.set(-distance, distance, -distance);
        this.scene.add(directionalLight4);
    
        const overheadLight = new THREE.DirectionalLight(0xffffff, 0.8);
        overheadLight.position.set(0, 500, 0);
        this.scene.add(overheadLight);
    
        const ambientLight = new THREE.AmbientLight(0x404040, 1.5);
        this.scene.add(ambientLight);
    
        const hemiLight = new THREE.HemisphereLight(0xffffff, 0x222222, 0.5);
        hemiLight.position.set(0, 100, 0);
        this.scene.add(hemiLight);
    
        // Add helpers
        const dirLightHelper1 = new THREE.DirectionalLightHelper(directionalLight1, 10);
        const dirLightHelper2 = new THREE.DirectionalLightHelper(directionalLight2, 10);
        const dirLightHelper3 = new THREE.DirectionalLightHelper(directionalLight3, 10);
        const dirLightHelper4 = new THREE.DirectionalLightHelper(directionalLight4, 10);
        const overheadLightHelper = new THREE.DirectionalLightHelper(overheadLight, 10);
        const hemiLightHelper = new THREE.HemisphereLightHelper(hemiLight, 10);
    
        this.scene.add(dirLightHelper1, dirLightHelper2, dirLightHelper3, dirLightHelper4, overheadLightHelper, hemiLightHelper);
    
        this.lights = [
            directionalLight1,
            directionalLight2,
            directionalLight3,
            directionalLight4,
            overheadLight,
            ambientLight,
            hemiLight,
        ];
    
        // Debug log
        console.log("Scene children with helpers:", this.scene.children);
    }
    


    setModelCustomProps(mesh, options) {
        mesh.material.color.set(options.color || 0xffffff);
        mesh.material.transparent = true;
        mesh.material.opacity =  options.opacity || 1.0;
        mesh.material.metalness = 0; // Non-metallic for better diffuse lighting
        mesh.material.roughness = 1; // High roughness for diffuse reflection
        mesh.material.side = THREE.DoubleSide; // Ensures backfaces are lit
        mesh.material.needsUpdate = true;
    
        mesh.castShadow = true;
        mesh.receiveShadow = true;
    }

    _setupGrid() {
        const gridHelper = new THREE.GridHelper(200, 20);
        this.scene.add(gridHelper);
    }

    _appendToContainer() {
        this.container.appendChild(this.renderer.domElement);
    }

    
    calcZWithBoundingSphere(boundingBox) {
        // a) Get the box center & size
        const center = boundingBox.getCenter(new THREE.Vector3());
        const size = boundingBox.getSize(new THREE.Vector3());
        
        // b) The radius is half the diagonal of the box (the "fit" sphere)
        const radius = size.length() / 2; 
        // Or, equivalently: 
        //   const halfDiagonal = Math.sqrt((size.x/2)**2 + (size.y/2)**2 + (size.z/2)**2);
        //   const radius = halfDiagonal;
    
        // c) Convert vertical FOV to radians
        const verticalFOV = this.camera.fov * (Math.PI / 180);
    
        // d) Distance required so the bounding sphere fits entirely
        //    in the camera frustum, ignoring aspect ratio for now.
        //    distance = radius / sin(verticalFOV/2)
        let distance = radius / Math.sin(verticalFOV / 2);
    
        // e) Apply optional padding factor
        const offset = 1.2; // e.g., 1.2 or 2.0, etc.
        distance *= offset;
    
        return { distance, center };
    }
    
    calc_z_for_auto_zoom(boundingBox) {
        // 1) Determine bounding box size
        const size = boundingBox.getSize(new THREE.Vector3());
    
        // 2) Convert camera FOV (vertical) from degrees to radians
        const verticalFOV = this.camera.fov * (Math.PI / 180);
    
        // 3) Calculate the camera’s horizontal FOV using the aspect ratio
        //    horizontalFOV = 2 * atan( tan(verticalFOV/2) * aspect )
        const aspect = this.camera.aspect;
        const horizontalFOV = 2 * Math.atan(Math.tan(verticalFOV / 2) * aspect);
    
        // 4) We’ll pick a padding factor for extra space around the model
        const offset = 2;
    
        // 5) Calculate the distance required so the model fits horizontally (dx)
        //    and vertically (dy), plus consider size.z if you want a safe margin in Z as well (dz).
        const dx = (size.x / 2) / Math.tan(horizontalFOV / 2); // fit width
        const dy = (size.y / 2) / Math.tan(verticalFOV / 2);   // fit height
        const dz = size.z / 2;  // a minimal offset if you’re also considering depth
    
        // 6) We pick the largest required distance for X, Y, or Z, then apply offset
        const distance = Math.max(dx, dy, dz) * offset;
    
        return distance;
    }
    

    updateCamera(boundingBox) {
        const size = boundingBox.getSize(new THREE.Vector3());
        const center = boundingBox.getCenter(new THREE.Vector3());
        
        // Calculate distance based on the largest dimension
        const maxDimension = Math.max(size.x, size.y, size.z);
        const fov = this.camera.fov * (Math.PI / 180);
        const cameraZ = maxDimension / (2 * Math.tan(fov / 2));
        
        // Add padding and set camera position
        const padding = 1.2;
        this.camera.position.set(
            center.x,
            center.y,
            center.z + (cameraZ * padding)
        );
        
        // Update camera parameters
        this.controls.target.copy(center);
        this.camera.near = maxDimension * 0.01;
        this.camera.far = maxDimension * 10;
        this.camera.updateProjectionMatrix();
        this.controls.update();
    }




    updateCameraToFitObject(object) {
        const boundingBox = new THREE.Box3().setFromObject(object);
        const size = boundingBox.getSize(new THREE.Vector3());
        const center = boundingBox.getCenter(new THREE.Vector3());
    
        const maxDim = Math.max(size.x, size.y, size.z);
        const fov = this.camera.fov * (Math.PI / 180); // Convert FOV to radians
        const cameraDistance = maxDim / (2 * Math.tan(fov / 2)); // Adjust based on FOV
    
        const offset = 1.1; // Optional: Add a margin
        const distance = cameraDistance * offset;
    
        // Position the camera to fit the object
        this.camera.position.set(center.x, center.y, center.z + distance);
        this.camera.lookAt(center);
    
        // Update near and far planes
        this.camera.near = 0.1;
        this.camera.far = distance * 3;
        this.camera.updateProjectionMatrix();
    }

    adjustLighting(boundingBox) {
        const center = boundingBox.getCenter(new THREE.Vector3());
        this.lights.forEach((light) => {
            if (light.isDirectionalLight) {
                light.position.set(center.x + 50, center.y + 50, center.z + 100);
            }
        });
    }

    render() {
        requestAnimationFrame(() => this.render());
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
    }

    setGrid(size = 200, divisions = 20) {
        if (this.gridHelper) {
            this.scene.remove(this.gridHelper);
        }
        this.gridHelper = new THREE.GridHelper(size, divisions);
        this.scene.add(this.gridHelper);
    }

    setModelCustomProps(mesh, options) {
        if (options.color) {
            mesh.material.color.set(options.color);
        }
        if (options.opacity !== undefined) {
            mesh.material.transparent = true;
            mesh.material.opacity = options.opacity;
        }
        if (options.metalness !== undefined) {
            mesh.material.metalness = options.metalness;
        }
        if (options.roughness !== undefined) {
            mesh.material.roughness = options.roughness;
        }
        mesh.material.needsUpdate = true;
        mesh.material.depthWrite = false; 
        mesh.material.receiveShadow = true;
        mesh.material.castShadow = true;
    }
    
}

export default ViewerRenderer;
