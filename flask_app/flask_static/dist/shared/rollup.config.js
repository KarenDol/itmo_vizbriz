import resolve from '@rollup/plugin-node-resolve';
import commonjs from '@rollup/plugin-commonjs';
import { terser } from 'rollup-plugin-terser';

export default [
  // Build UMD bundle for @cornerstonejs/core
  {
    input: 'node_modules/@cornerstonejs/core/dist/index.js',  // adjust if needed
    output: {
      file: '/home/ec2-user/python-patient-data/flask_app/flask_static/dist/cornerstoneCore.umd.js',
      format: 'umd',
      name: 'cornerstoneCore'
    },
    plugins: [resolve(), commonjs(), terser()]
  },
  // Build UMD bundle for @cornerstonejs/dicom-image-loader
  {
    input: 'node_modules/@cornerstonejs/dicom-image-loader/dist/index.js',  // adjust if needed
    output: {
      file: '/home/ec2-user/python-patient-data/flask_app/flask_static/dist/cornerstoneDicomImageLoader.umd.js',
      format: 'umd',
      name: 'cornerstoneDicomImageLoader',
      globals: {
        '@cornerstonejs/core': 'cornerstoneCore'
      }
    },
    plugins: [resolve(), commonjs(), terser()]
  },
  // Build UMD bundle for @cornerstonejs/streaming-image-volume-loader
  {
    input: 'node_modules/@cornerstonejs/streaming-image-volume-loader/dist/index.js',  // adjust if needed
    output: {
      file: '/home/ec2-user/python-patient-data/flask_app/flask_static/dist/cornerstoneStreamingImageVolumeLoader.umd.js',
      format: 'umd',
      name: 'cornerstoneStreamingImageVolumeLoader',
      globals: {
        '@cornerstonejs/core': 'cornerstoneCore',
        '@cornerstonejs/dicom-image-loader': 'cornerstoneDicomImageLoader'
      }
    },
    plugins: [resolve(), commonjs(), terser()]
  },
  // Build UMD bundle for @cornerstonejs/tools
  {
    input: 'node_modules/@cornerstonejs/tools/dist/index.js',  // adjust if needed
    output: {
      file: '/home/ec2-user/python-patient-data/flask_app/flask_static/dist/cornerstoneTools.umd.js',
      format: 'umd',
      name: 'cornerstoneTools',
      globals: {
        '@cornerstonejs/core': 'cornerstoneCore'
      }
    },
    plugins: [resolve(), commonjs(), terser()]
  }
];
