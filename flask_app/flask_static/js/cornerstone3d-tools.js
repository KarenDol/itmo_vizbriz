// static/js/cornerstone3d-tools.js
import { addTool, setToolActive } from '@cornerstonejs/tools';
import {
  LengthTool,
  RectangleROITool,
  EllipticalROITool,
  ProbeTool,
  BrushTool,
  StackScrollMouseWheelTool,
} from '@cornerstonejs/tools';

// Initialize Cornerstone3D Tools
addTool(LengthTool);
addTool(RectangleROITool);
addTool(EllipticalROITool);
addTool(ProbeTool);
addTool(BrushTool);
addTool(StackScrollMouseWheelTool);

// Set the default active tool
setToolActive('StackScrollMouseWheel', { mouseButtonMask: 1 });
