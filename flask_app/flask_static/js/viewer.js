document.addEventListener('DOMContentLoaded', () => {
    let files = [];
    let filteredFiles = [];
    let renderer, scene, camera, controls;
    let selectedMeshes = [];
    const objectSettings = new Map();

    // DOM Elements
    const filesList = document.getElementById('files');
    const submitButton = document.getElementById('load-selected');
    const toggleFileListButton = document.getElementById('toggle-file-list');
    const toggleControlsButton = document.getElementById('toggle-controls');
    const fileListContainer = document.getElementById('file-list-container');
    const controlsContainer = document.getElementById('controls-container');
    const viewerContainer = document.getElementById('viewer');
    const titleContainer = document.getElementById('title-container');
    const viewer = document.getElementById('viewer');
    const patientId = document.getElementById('patient-info').dataset.patientId;

    // Initialize the viewer
    initScene();
    createConfigurationSliders();
    fetchFiles();

    // Event Listeners
    if (submitButton) {
        submitButton.addEventListener('click', handleSubmit);
    }

    if (filesList) {
        filesList.addEventListener('click', handleFileSelection);
    }

    if (toggleFileListButton) {
        toggleFileListButton.addEventListener('click', () => {
            toggleVisibility(fileListContainer, titleContainer, toggleFileListButton, true);
        });
    }

    if (toggleControlsButton) {
        toggleControlsButton.addEventListener('click', () => {
            toggleVisibility(controlsContainer, null, toggleControlsButton, false);
        });
    }

    

    /**
     * Toggles visibility of the file list or controls container
     * @param {HTMLElement} container - The container to toggle visibility for
     * @param {HTMLElement|null} header - The optional header to toggle visibility for
     * @param {HTMLElement} button - The button triggering the toggle
     * @param {boolean} isFileList - If true, adjust the viewer for the file list; otherwise for controls
     */
    function toggleVisibility(container, header, button, isFileList) {
        if (container.style.display === 'none' || !container.style.display) {
            container.style.display = 'block';
            if (header) header.style.display = 'block'; // Show header if applicable
            container.style.flex = isFileList ? '0 0 200px' : '0 0 300px';
            viewerContainer.style.flex = '1';
            button.innerHTML = isFileList ? '&#x276E;' : '&#x276F;';
        } else {
            container.style.display = 'none';
            if (header) header.style.display = 'none'; // Hide header if applicable
            container.style.flex = '0 0 0';
            viewerContainer.style.flex = '1'; // Expand viewer
            button.innerHTML = isFileList ? '&#x276F;' : '&#x276E;';
        }
    }

    /**
     * Initialize the 3D Scene
     */
    function initScene() {
        scene = new THREE.Scene();

        // Camera setup
        camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 1000);
        camera.position.set(0, 0, 150);

        // Renderer setup
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setSize(window.innerWidth, window.innerHeight);
        renderer.setPixelRatio(window.devicePixelRatio);
        renderer.setClearColor(0x000000, 1);
        renderer.outputEncoding = THREE.sRGBEncoding;
        renderer.shadowMap.enabled = true;
        renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        renderer.toneMapping = THREE.ACESFilmicToneMapping;

        // Append the renderer to the DOM
        document.getElementById('dicom-container').appendChild(renderer.domElement);

        // Orbit Controls
        controls = new THREE.OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.05;
        controls.screenSpacePanning = true;

        // Add Lights
        addLighting();

        // Render Scene
        renderScene();
    }

    /**
     * Add Lighting to the Scene
     */
    function addLighting() {
        const frontLight = new THREE.DirectionalLight(0xffffff, 3);
        frontLight.position.set(1, 1, 2);
        scene.add(frontLight);

        const backLight = new THREE.DirectionalLight(0xffffff, 2.5);
        backLight.position.set(-1, -1, -2);
        scene.add(backLight);

        const ambientLight = new THREE.AmbientLight(0xffffff, 1.5);
        scene.add(ambientLight);
    }

    /**
     * Fetch Files from the Server
     */
    async function fetchFiles() {
        try {
            const response = await fetch(`/viewer/files/${patientId}`);
            const data = await response.json();
            files = data.files || [];
            applyDefaultStlFilter();
        } catch (err) {
            console.error('Error fetching files:', err);
        }
    }

    /**
     * Apply STL Filter to Files
     */
    function applyDefaultStlFilter() {
        filteredFiles = files.filter(file => file.file_name.toLowerCase().endsWith('.stl'));
        populateFileList();
    }

    /**
     * Populate the File List UI
     */
    function populateFileList() {
        filesList.innerHTML = '';
        filteredFiles.forEach(file => {
            const li = document.createElement('li');
            li.textContent = file.file_name;
            li.dataset.url = file.url;
            filesList.appendChild(li);
        });
    }

    /**
     * Handle File Selection
     */
    function handleFileSelection(event) {
        if (event.target.tagName === 'LI') {
            event.target.classList.toggle('selected');
        }
    }

    /**
     * Handle File Submission
     */
    function handleSubmit() {
        const selectedFiles = Array.from(filesList.querySelectorAll('li.selected'))
            .map(li => li.dataset.url);

        if (selectedFiles.length > 0) {
            loadMultipleSTL(selectedFiles);
        } else {
            alert('Please select at least one STL file to load.');
        }
    }

    /**
     * Load Multiple STL Files
     */
    function loadMultipleSTL(selectedFiles) {
        disposeScene();
        selectedMeshes = [];
        const combinedBoundingBox = new THREE.Box3();
        const loader = new THREE.STLLoader();

        let filesLoaded = 0;
        selectedFiles.forEach((url, index) => {
            const fileName = url.split('/').pop().split('.')[0];
            loader.load(
                url,
                (geometry) => {
                    const material = createDefaultMaterial(index);
                    const mesh = new THREE.Mesh(geometry, material);
                    mesh.name = fileName;

                    // Center and Scale Geometry
                    geometry.computeBoundingBox();
                    geometry.center();
                    const size = geometry.boundingBox.getSize(new THREE.Vector3());
                    const scale = 100 / Math.max(size.x, size.y, size.z);
                    mesh.scale.set(scale, scale, scale);

                    // Add to Scene
                    mesh.castShadow = true;
                    mesh.receiveShadow = true;
                    scene.add(mesh);
                    selectedMeshes.push(mesh);

                    combinedBoundingBox.union(geometry.boundingBox);

                    // Update Scene and Camera after all files are loaded
                    filesLoaded++;
                    if (filesLoaded === selectedFiles.length) {
                        setupCamera(combinedBoundingBox);
                        updateObjectSelector();
                        renderScene();
                    }
                },
                undefined,
                (error) => console.error(`Error loading STL file: ${error}`)
            );
        });
    }

    /**
     * Create a Default Material for Mesh
     */
    function createDefaultMaterial(index) {
        const colors = [0xffffff, 0x808080, 0x4287f5, 0xf54242, 0x42f54b];
        return new THREE.MeshStandardMaterial({
            color: colors[index % colors.length],
            roughness: 0.4,
            metalness: 0.6,
            side: THREE.DoubleSide,
            envMapIntensity: 1.0,
        });
    }

    /**
     * Dispose the Current Scene
     */
    function disposeScene() {
        while (scene.children.length > 0) {
            const object = scene.children[0];
            scene.remove(object);
        }
        selectedMeshes = [];
        addLighting();
    }

    /**
     * Setup Camera to Fit Scene
     */
    function setupCamera(boundingBox) {
        const center = boundingBox.getCenter(new THREE.Vector3());
        const size = boundingBox.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);
        const distance = maxDim / (2 * Math.tan((camera.fov * Math.PI) / 360));

        camera.position.set(center.x, center.y, distance * 2.5);
        camera.lookAt(center);
        camera.near = 0.1;
        camera.far = distance * 4;
        camera.updateProjectionMatrix();
    }

    /**
     * Render the Scene
     */
    function renderScene() {
        requestAnimationFrame(renderScene);
        controls.update();
        renderer.render(scene, camera);
    }

    /**
     * Create Configuration Sliders
     */
    function createConfigurationSliders() {
        const objectSelector = document.getElementById('object-selector');
        const roughnessSlider = document.getElementById('roughness');
        const metalnessSlider = document.getElementById('metalness');
        const colorPicker = document.getElementById('object-color');

        if (roughnessSlider) {
            roughnessSlider.addEventListener('input', (event) => {
                updateMaterialProperty('roughness', parseFloat(event.target.value));
            });
        }

        if (metalnessSlider) {
            metalnessSlider.addEventListener('input', (event) => {
                updateMaterialProperty('metalness', parseFloat(event.target.value));
            });
        }

        if (colorPicker) {
            colorPicker.addEventListener('input', (event) => {
                updateMaterialProperty('color', event.target.value);
            });
        }

        if (objectSelector) {
            objectSelector.addEventListener('change', updateSlidersForSelection);
        }
    }

    /**
     * Update Material Properties
     */
    function updateMaterialProperty(property, value) {
        const objectSelector = document.getElementById('object-selector');
        const selectedIndex = objectSelector.value;

        if (selectedIndex === 'all') {
            selectedMeshes.forEach(mesh => {
                if (mesh.material) {
                    applyMaterialProperty(mesh, property, value);
                }
            });
        } else {
            const selectedMesh = selectedMeshes[parseInt(selectedIndex)];
            if (selectedMesh && selectedMesh.material) {
                applyMaterialProperty(selectedMesh, property, value);
            }
        }
    }

    /**
     * Helper to Apply Material Properties
     */
    function applyMaterialProperty(mesh, property, value) {
        if (!mesh || !mesh.material) return;

        const material = mesh.material;

        if (property === 'color') {
            material.color.set(value);
        } else {
            material[property] = value;
        }

        material.needsUpdate = true;
    }

    /**
     * Update Object Selector
     */
    function updateObjectSelector() {
        const objectSelector = document.getElementById('object-selector');
        objectSelector.innerHTML = '<option value="all">All Objects</option>';
        selectedMeshes.forEach((mesh, index) => {
            const option = document.createElement('option');
            option.value = index;
            option.textContent = mesh.name || `Object ${index + 1}`;
            objectSelector.appendChild(option);
        });
    }

    /**
     * Update Sliders for Selection
     */
    function updateSlidersForSelection() {
        const objectSelector = document.getElementById('object-selector');
        const roughnessSlider = document.getElementById('roughness');
        const metalnessSlider = document.getElementById('metalness');
        const colorPicker = document.getElementById('object-color');

        const selectedIndex = objectSelector.value;

        if (selectedIndex === 'all') {
            if (roughnessSlider) roughnessSlider.value = 0.4;
            if (metalnessSlider) metalnessSlider.value = 0.6;
            if (colorPicker) colorPicker.value = '#808080';
        } else {
            const selectedMesh = selectedMeshes[parseInt(selectedIndex)];
            if (selectedMesh && selectedMesh.material) {
                if (roughnessSlider) roughnessSlider.value = selectedMesh.material.roughness || 0.4;
                if (metalnessSlider) metalnessSlider.value = selectedMesh.material.metalness || 0.6;
                if (colorPicker) colorPicker.value = `#${selectedMesh.material.color.getHexString()}`;
            }
        }
    }
});

const fullscreenButton = document.getElementById('fullscreen-btn');
fullscreenButton.addEventListener('click', () => {
    const elem = document.documentElement; // Full document
    if (!document.fullscreenElement) {
        elem.requestFullscreen().catch(err => {
            console.error(`Error attempting to enable full-screen mode: ${err.message}`);
        });
    } else {
        document.exitFullscreen();
    }
});