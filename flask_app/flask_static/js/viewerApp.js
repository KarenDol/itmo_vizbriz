import ViewerRenderer from './ViewerRenderer.js';

document.addEventListener('DOMContentLoaded', () => {
    const viewerContainer = document.getElementById('viewer');
    const filesList = document.getElementById('files');
    const submitButton = document.getElementById('load-selected');
    const objectSelector = document.getElementById('object-selector');
    const materialTypeSelector = document.getElementById('material-type'); // Add material type dropdown
    const colorPicker = document.getElementById('object-color'); // Reference the color picker
    const roughnessSlider = document.getElementById('roughness'); // Reference the roughness slider
    const opacitySlider = document.getElementById('opacity'); // Reference opacity slider
    const boundingBoxToggle = document.getElementById('bounding-box-toggle'); // Reference bounding box toggle
    const gridToggle = document.getElementById('grid-toggle'); // Grid toggle
    const fullscreenButton = document.getElementById('fullscreen-btn'); // Ensure the ID matches the HTML
    
    // Reference light sliders
    const ambientLightSlider = document.getElementById('ambient-light-intensity');
    const hemisphereLightSlider = document.getElementById('hemisphere-light-intensity');
    const directionalLight1Slider = document.getElementById('directional-light1-intensity');
    const directionalLight2Slider = document.getElementById('directional-light2-intensity');
    const toggleFileListButton = document.getElementById('toggle-file-list');
    const fileListContainer = document.getElementById('file-list-container');
    const toggleControlsButton = document.getElementById('toggle-controls');
    const controlsContainer = document.getElementById('controls-container');
    
    // Position control sliders
    const positionXSlider = document.getElementById('position-x');
    const positionYSlider = document.getElementById('position-y');
    const positionZSlider = document.getElementById('position-z');
    const resetPositionBtn = document.getElementById('reset-position');
    
    // Store original positions for each mesh
    let originalPositions = new Map();
    
    // Left arrow hides/shows the left panel
    toggleFileListButton.addEventListener('click', () => {
        fileListContainer.classList.toggle('hidden');
         // If fileListContainer is now hidden, arrow should point to the right
        if (fileListContainer.classList.contains('hidden')) {
            toggleFileListButton.textContent = '\u276F'; // Right arrow: ›
        } else {
            toggleFileListButton.textContent = '\u276E'; // Left arrow: ‹
        }
    });

    // Le arrow hides/shows the right panel
    toggleControlsButton.addEventListener('click', () => {
        controlsContainer.classList.toggle('hidden');
        if (controlsContainer.classList.contains('hidden')) {
            toggleControlsButton.textContent = '\u276E'; // Left arrow: ‹
        } else {
            toggleControlsButton.textContent = '\u276F'; // Right arrow: ›
        }
    });


    let ambientLight, hemisphereLight, directionalLight1, directionalLight2;


    let renderer = new ViewerRenderer(viewerContainer);
    renderer.initialize();

    // ------------------------------------------------------------------
    // -- ADDED: Map renderer.lights to your local variables so sliders --
    // --       adjust the actual lights from ViewerRenderer.          --
    // ------------------------------------------------------------------
    if (renderer.lights && renderer.lights.length > 0) {
        // By default, in ViewerRenderer._setupLighting():
        //   this.lights = [directionalLight, ambientLight, hemiLight];
        // If you truly want a second directional light, you'd add it in ViewerRenderer.
        directionalLight1 = renderer.lights[0];
        ambientLight      = renderer.lights[1];
        hemisphereLight   = renderer.lights[2];

        }
    // ------------------------------------------------------------------

    let sceneMeshes = [];
    let boundingBoxes = []; // Array to store bounding box helpers
    let gridHelper = null; // To store the grid helper object
    


    function adjustCameraToBoundingBox(boundingBox) {
        const size = boundingBox.getSize(new THREE.Vector3());
        const center = boundingBox.getCenter(new THREE.Vector3());

        const maxDim = Math.max(size.x, size.y, size.z);
        const fov = renderer.camera.fov * (Math.PI / 180); // Convert FOV to radians
        const cameraZ = Math.abs(maxDim / (2 * Math.tan(fov / 2)));

        renderer.camera.position.set(center.x, center.y, cameraZ * 1.5); // Add padding
        renderer.camera.lookAt(center);

        renderer.camera.near = 0.1;
        renderer.camera.far = cameraZ + maxDim * 2;
        renderer.camera.updateProjectionMatrix();
    }


    function populateObjectSelector() {
        objectSelector.innerHTML = '<option value="all">All Objects</option>';
        sceneMeshes.forEach((mesh, index) => {
            console.log(`Processing mesh at index ${index}:`, mesh); // Log each mesh object
            console.log(`Mesh name: "${mesh.name}"`); // Log the name of the mesh
            const option = document.createElement('option');
            option.value = index;
            option.textContent = mesh.name || `Object ${index + 1}`; // Use mesh name or fallback
            objectSelector.appendChild(option);
        });
        
        // Reset position controls and clear stored positions
        originalPositions.clear();
        if (positionXSlider) {
            positionXSlider.value = 0;
            document.getElementById('position-x-value').textContent = '0';
        }
        if (positionYSlider) {
            positionYSlider.value = 0;
            document.getElementById('position-y-value').textContent = '0';
        }
        if (positionZSlider) {
            positionZSlider.value = 0;
            document.getElementById('position-z-value').textContent = '0';
        }
    }

    function showLoadingSpinner() {
        const spinner = document.getElementById('loading-spinner');
        spinner.style.display = 'block'; // Show spinner
    }
    
    function hideLoadingSpinner() {
        const spinner = document.getElementById('loading-spinner');
        spinner.style.display = 'none'; // Hide spinner
    }

    // Function to toggle selection of file list items
    function setupFileListSelection() {
        const fileItems = filesList.querySelectorAll('li');
        fileItems.forEach((item) => {
            item.addEventListener('click', () => {
                item.classList.toggle('selected'); // Toggle 'selected' class
            });
        });
    }

    async function fetchFiles(patientId) {
        try {
            const response = await fetch(`/viewer/files/${patientId}`);
            const data = await response.json();
            populateFileList(data.files || []);
        } catch (error) {
            console.error('Error fetching files:', error);
        }
    }

    function populateFileList(files) {
        filesList.innerHTML = ''; // Clear the list
        files.forEach((file) => {
            const listItem = document.createElement('li');
            listItem.classList.add('file-item');
            listItem.dataset.url = file.url;
            listItem.textContent = file.file_name;
            filesList.appendChild(listItem);
        });

        // Set up click event listeners for the new list items
        setupFileListSelection();
    }

    function resetScene() {
        // Instead of removing *everything*:
        // while (renderer.scene.children.length > 0) {
        //   const object = renderer.scene.children[0];
        //   renderer.scene.remove(object);
        // }
      
        // Remove only non-light, non-grid objects:
        const objectsToRemove = renderer.scene.children.filter((obj) => {
          return !obj.isLight && !obj.isGridHelper;
        });
        objectsToRemove.forEach((obj) => {
          renderer.scene.remove(obj);
        });
      
        // Clear the sceneMeshes array, bounding boxes, etc.
        sceneMeshes.length = 0;
        boundingBoxes = [];
        gridHelper = null;
      
        // Now we do NOT call renderer.initializeScene(), 
        // so the original lights remain. 
      }

    function resetView() {
        const combinedBoundingBox = new THREE.Box3();
        sceneMeshes.forEach(mesh => {
            combinedBoundingBox.expandByObject(mesh);
        });
        renderer.updateCamera(combinedBoundingBox);
    }

    function loadSTLFiles(fileUrls) {
        // Wrapper for backward compatibility - calls the new unified loader
        load3DFiles(fileUrls);
    }
    
    function load3DFiles(fileUrls) {
        // 1) Show a spinner indicating we're loading files
        showLoadingSpinner();
        
        // 2) Reset the scene (remove old objects, re-setup lights/grids, etc.)
        resetScene();
    
        // 3) Create loaders for different file types
        const stlLoader = new THREE.STLLoader();
        const plyLoader = new THREE.PLYLoader();
    
        // 4) Create a group to hold all the loaded meshes
        const group = new THREE.Group();
        renderer.scene.add(group);
    
        // 5) We'll track how many files have completed loading
        let filesLoaded = 0;
    
        // 6) Clear our global array of meshes
        sceneMeshes.length = 0;
    
        // Helper function to finalize loading when all files are done
        function onAllFilesLoaded() {
            // Apply rotation to the ENTIRE GROUP (preserves relative positioning)
            // This ensures all meshes from the same coordinate system stay aligned
            group.rotateY(Math.PI/2 * 3);
            group.rotateX(Math.PI/2 * 3);
            
            // Compute bounding box for the entire group AFTER rotation
            const groupBBox = new THREE.Box3().setFromObject(group);
            const { distance, center } = renderer.calcZWithBoundingSphere(groupBBox);
            renderer.camera.position.set(center.x, center.y, center.z + distance);
            renderer.controls.target.copy(center);
            renderer.camera.updateProjectionMatrix();
            
            // Hide the spinner
            hideLoadingSpinner();
            console.log("All 3D files loaded into sceneMeshes:", sceneMeshes);
            
            // Populate the object selector dropdown
            populateObjectSelector();
        }
    
        // Helper function to process loaded geometry
        function processGeometry(geometry, url, index, hasVertexColors = false) {
            // Compute vertex normals for smoother lighting
            geometry.computeVertexNormals();
            geometry.computeBoundingBox();
    
            // Create material - use vertex colors if available (PLY files)
            let material;
            if (hasVertexColors && geometry.attributes.color) {
                // PLY with vertex colors - use material that shows the colors
                material = new THREE.MeshStandardMaterial({
                    vertexColors: true,
                    roughness: 0.8,
                    metalness: 0.1,
                    side: THREE.DoubleSide
                });
                console.log("Using vertex colors for:", url);
            } else {
                // STL or PLY without colors - use standard material
                material = createMaterial("lambert", index);
            }
    
            // Create the mesh
            const mesh = new THREE.Mesh(geometry, material);
            
            if (!hasVertexColors) {
                renderer.setModelCustomProps(mesh, {
                    opacity: 1.0,
                    metalness: 0,
                    roughness: 1
                });
            }
    
            // Extract file name without extension
            const fileName = url.split("/").pop().split(".")[0];
            mesh.name = fileName;
            console.log("Mesh name being assigned:", mesh.name);
    
            // NOTE: Rotations are now applied to the GROUP, not individual meshes
            // This preserves the relative positioning between meshes from the same coordinate system
    
            // Shadows
            mesh.castShadow = false;
            mesh.receiveShadow = true;
    
            // Add to group and global array
            group.add(mesh);
            sceneMeshes.push(mesh);
    
            // Check if all files are loaded
            filesLoaded++;
            if (filesLoaded === fileUrls.length) {
                onAllFilesLoaded();
            }
        }
    
        // 7) Loop over each file URL to load them asynchronously
        fileUrls.forEach((url, index) => {
            const extension = url.split('.').pop().toLowerCase().split('?')[0]; // Handle URLs with query params
            
            if (extension === 'ply') {
                // Load PLY file (may have vertex colors)
                plyLoader.load(
                    url,
                    (geometry) => {
                        const hasColors = geometry.attributes.color !== undefined;
                        processGeometry(geometry, url, index, hasColors);
                    },
                    undefined,
                    (error) => {
                        console.error("Error loading PLY:", error);
                        filesLoaded++;
                        if (filesLoaded === fileUrls.length) {
                            onAllFilesLoaded();
                        }
                    }
                );
            } else {
                // Load STL file (no vertex colors)
                stlLoader.load(
                    url,
                    (geometry) => {
                        processGeometry(geometry, url, index, false);
                    },
                    undefined,
                    (error) => {
                        console.error("Error loading STL:", error);
                        filesLoaded++;
                        if (filesLoaded === fileUrls.length) {
                            onAllFilesLoaded();
                        }
                    }
                );
            }
        });
    }
    
    
       

    function createMaterial(type, index) {
        const colors = [0xffffff, 0x808080, 0x4287f5, 0xf54242, 0x42f54b];
        const color = colors[index % colors.length] || 0xffffff; // Fallback to white if index is invalid
    
        switch (type) {
            case 'lambert':
                return new THREE.MeshLambertMaterial({
                    color,
                });
            case 'phong':
                return new THREE.MeshPhongMaterial({
                    color,
                    shininess: 100,
                });
            case 'standard':
                return new THREE.MeshStandardMaterial({
                    color,
                    roughness: 0.5,
                    metalness: 0.7,
                });
            case 'physical':
                return new THREE.MeshPhysicalMaterial({
                    color,
                    roughness: 0.5,
                    metalness: 0.7,
                    clearcoat: 0.5,
                });
            default:
                return new THREE.MeshStandardMaterial({
                    color,
                });
        }
    }

    function scaleForMobile() {
        const scaleFactor = window.innerWidth < 768 ? 0.5 : 1; // Smaller scale for mobile
        sceneMeshes.forEach((mesh) => {
            mesh.scale.set(scaleFactor, scaleFactor, scaleFactor);
        });
    }

        
    // Event listener for color picker
    colorPicker.addEventListener('input', (event) => {
        const color = event.target.value; // Get the selected color
        const selectedIndex = objectSelector.value; // Get selected object index
        const selectedMeshes = selectedIndex === 'all' ? sceneMeshes : [sceneMeshes[selectedIndex]];

        // Apply color to selected meshes
        selectedMeshes.forEach((mesh) => {
            mesh.material.color.set(color);
            mesh.material.needsUpdate = true; // Ensure material is updated
        });

        console.log('Color updated to:', color); // Debug log
    });


    materialTypeSelector.addEventListener('change', () => {
        const materialType = materialTypeSelector.value;
        const selectedIndex = objectSelector.value;

        const selectedMeshes =
            selectedIndex === 'all' ? sceneMeshes : [sceneMeshes[selectedIndex]];

        selectedMeshes.forEach((mesh) => {
            const newMaterial = createMaterial(materialType, 0); // Default index for color
            mesh.material = newMaterial;
        });
    });

    // Event listener for roughness slider
    roughnessSlider.addEventListener('input', (event) => {
        const roughness = parseFloat(event.target.value); // Get the selected roughness value
        const selectedIndex = objectSelector.value; // Get selected object index
        const selectedMeshes = selectedIndex === 'all' ? sceneMeshes : [sceneMeshes[selectedIndex]];

        // Apply roughness to selected meshes
        selectedMeshes.forEach((mesh) => {
            if (mesh.material.isMeshStandardMaterial || mesh.material.isMeshPhysicalMaterial) {
                mesh.material.roughness = roughness;
                mesh.material.needsUpdate = true; // Ensure material is updated
            }
        });

        console.log('Roughness updated to:', roughness); // Debug log
    });

    
    //Event listener for opacity slider
    opacitySlider.addEventListener('input', (event) => {
        const opacity = parseFloat(event.target.value);
        const selectedIndex = objectSelector.value;
        const selectedMeshes =
            selectedIndex === 'all' ? sceneMeshes : [sceneMeshes[selectedIndex]];

        selectedMeshes.forEach((mesh) => {
            mesh.material.transparent = true;
            mesh.material.opacity = opacity;
            mesh.material.needsUpdate = true;
        });

        console.log('Opacity updated to:', opacity);
    });

    // Event listener for bounding box toggle
    boundingBoxToggle.addEventListener('change', (event) => {
        const showBoundingBox = event.target.checked; // Get toggle state
        const selectedIndex = objectSelector.value; // Get selected object index
        const selectedMeshes = selectedIndex === 'all' ? sceneMeshes : [sceneMeshes[selectedIndex]];
    
        // Clear all existing bounding boxes
        boundingBoxes.forEach((box) => renderer.scene.remove(box));
        boundingBoxes = [];
    
        if (showBoundingBox) {
            // Add bounding boxes to selected meshes
            selectedMeshes.forEach((mesh) => {
                const boxHelper = new THREE.BoxHelper(mesh, 0xffff00); // Yellow bounding box
                renderer.scene.add(boxHelper);
                boundingBoxes.push(boxHelper);
            });
        }
    
        console.log('Bounding Box visibility updated:', showBoundingBox); // Debug log
    });

    gridToggle.addEventListener('change', (event) => {
        const showGrid = event.target.checked;

        if (showGrid) {
            gridHelper = new THREE.GridHelper(200, 20); // Size 200, divisions 20
            renderer.scene.add(gridHelper);
        } else {
            if (gridHelper) {
                renderer.scene.remove(gridHelper);
                gridHelper = null;
            }
        }

        console.log('Grid visibility updated:', showGrid);
    });

    // Position control event listeners
    function updatePosition(axis, value) {
        const selectedIndex = objectSelector.value;
        const selectedMeshes = selectedIndex === 'all' ? sceneMeshes : [sceneMeshes[selectedIndex]];
        
        selectedMeshes.forEach((mesh) => {
            // Store original position if not already stored
            if (!originalPositions.has(mesh.uuid)) {
                originalPositions.set(mesh.uuid, {
                    x: mesh.position.x,
                    y: mesh.position.y,
                    z: mesh.position.z
                });
            }
            
            const original = originalPositions.get(mesh.uuid);
            if (axis === 'x') mesh.position.x = original.x + parseFloat(value);
            if (axis === 'y') mesh.position.y = original.y + parseFloat(value);
            if (axis === 'z') mesh.position.z = original.z + parseFloat(value);
        });
    }

    if (positionXSlider) {
        positionXSlider.addEventListener('input', (event) => {
            const value = event.target.value;
            document.getElementById('position-x-value').textContent = value;
            updatePosition('x', value);
        });
    }

    if (positionYSlider) {
        positionYSlider.addEventListener('input', (event) => {
            const value = event.target.value;
            document.getElementById('position-y-value').textContent = value;
            updatePosition('y', value);
        });
    }

    if (positionZSlider) {
        positionZSlider.addEventListener('input', (event) => {
            const value = event.target.value;
            document.getElementById('position-z-value').textContent = value;
            updatePosition('z', value);
        });
    }

    if (resetPositionBtn) {
        resetPositionBtn.addEventListener('click', () => {
            // Reset all meshes to original positions
            sceneMeshes.forEach((mesh) => {
                if (originalPositions.has(mesh.uuid)) {
                    const original = originalPositions.get(mesh.uuid);
                    mesh.position.set(original.x, original.y, original.z);
                }
            });
            
            // Reset sliders
            if (positionXSlider) {
                positionXSlider.value = 0;
                document.getElementById('position-x-value').textContent = '0';
            }
            if (positionYSlider) {
                positionYSlider.value = 0;
                document.getElementById('position-y-value').textContent = '0';
            }
            if (positionZSlider) {
                positionZSlider.value = 0;
                document.getElementById('position-z-value').textContent = '0';
            }
            
            console.log('Positions reset to original');
        });
    }

    // Event listener for fullscreen button
    fullscreenButton.addEventListener('click', () => {
        const viewer = document.getElementById('viewer'); // Reference the viewer container

        if (!document.fullscreenElement) {
            viewer.requestFullscreen().catch((err) => {
                console.error(`Error attempting to enable full-screen mode: ${err.message}`);
            });
        } else {
            document.exitFullscreen().catch((err) => {
                console.error(`Error attempting to exit full-screen mode: ${err.message}`);
            });
        }
    });

    // Update Ambient Light Intensity
    ambientLightSlider.addEventListener('input', (event) => {
        const intensity = parseFloat(event.target.value);
        if (ambientLight) {
            ambientLight.intensity = intensity;
        }
        console.log('Ambient Light Intensity:', intensity);
    });

    // Update Hemisphere Light Intensity
    hemisphereLightSlider.addEventListener('input', (event) => {
        const intensity = parseFloat(event.target.value);
        if (hemisphereLight) {
            hemisphereLight.intensity = intensity;
        }
        console.log('Hemisphere Light Intensity:', intensity);
    });

    // Update Directional Light 1 Intensity
  /*  directionalLight1Slider.addEventListener('input', (event) => {
        const intensity = parseFloat(event.target.value);
        if (directionalLight1) {
            directionalLight1.intensity = intensity;
        }
        console.log('Directional Light 1 Intensity:', intensity);
    });*/

  /*    // Update Directional Light 2 Intensity
      directionalLight2Slider.addEventListener('input', (event) => {
        const intensity = parseFloat(event.target.value);
        if (directionalLight2) {
            directionalLight2.intensity = intensity;
        }
        console.log('Directional Light 2 Intensity:', intensity);
    });

    */

    window.addEventListener('resize', () => {
        const width = viewerContainer.clientWidth;
        const height = viewerContainer.clientHeight;
    
        renderer.camera.aspect = width / height;
        renderer.camera.updateProjectionMatrix();
        renderer.renderer.setSize(width, height);
    });

    if (submitButton) {
        submitButton.addEventListener('click', () => {
            const selectedFiles = Array.from(filesList.querySelectorAll('li.selected'))
                .map((li) => li.dataset.url);

            if (selectedFiles.length > 0) {
                loadSTLFiles(selectedFiles);
            } else {
                alert('Please select at least one STL file to load.');
            }
        });
    }

    // Fetch and populate the file list (assuming patientId is globally available)
    const patientId = document.getElementById('patient-info').dataset.patientId;
    fetchFiles(patientId);
});