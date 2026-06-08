
{% extends "base.html" %}

{% block title %}Patient Journey - {{ patient.name }} | Dr. Briz{% endblock %}

{% block content %}
<style>
/* Override base.html layout constraints for patient journey */
main {
  margin-left: 0 !important;
  padding-top: 64px !important;
  width: 100% !important;
  max-width: 100% !important;
  overflow-x: hidden !important;
}

.container {
  max-width: none !important;
  width: 100% !important;
  padding: 0 !important;
  margin: 0 !important;
  overflow-x: hidden !important;
}

/* Patient journey specific overrides - hamburger menu now handled globally in base.html */

/* Prevent unnecessary horizontal scrolling */
body {
  overflow-x: hidden !important;
  width: 100% !important;
  max-width: 100vw !important;
}

/* Material Design Colors and Variables */
:root {
  --primary-color: #2196F3;
  --primary-dark: #1976D2;
  --primary-light: #BBDEFB;
  --accent-color: #FF4081;
  --text-primary: #212121;
  --text-secondary: #757575;
  --divider-color: #BDBDBD;
  --background: #FAFAFA;
  --surface: #FFFFFF;
  --error: #F44336;
  --success: #4CAF50;
  --warning: #FF9800;
}

body {
  background: var(--background);
  color: var(--text-primary);
  font-family: 'Roboto', 'Segoe UI', sans-serif;
  margin: 0;
  padding: 0;
}

.journey-container {
  max-width: 1400px;
  margin: 0 0 0 16px; /* Reduced left margin to use more screen space */
  padding: 20px 20px 20px 0;
  width: calc(100vw - 32px) !important;
  overflow-x: hidden !important;
}

@media (max-width: 1200px) {
  .journey-container {
    margin-left: 8px;
    padding: 16px 16px 16px 0;
  }
}
@media (max-width: 900px) {
  .journey-container {
    margin-left: 0;
    padding-left: 0;
  }
}

/* Header Section */
.journey-header {
  background: var(--surface);
  border-radius: 8px;
  padding: 24px;
  margin-bottom: 24px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.journey-title {
  font-size: 28px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.patient-info {
  color: var(--text-secondary);
  font-size: 14px;
  margin-bottom: 16px;
}

.progress-section {
  margin-top: 16px;
}

.progress-label {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.progress-text {
  font-size: 16px;
  color: var(--text-secondary);
  margin-bottom: 12px;
}

.progress-bar {
  height: 8px;
  background: #E0E0E0;
  border-radius: 4px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--primary-color), var(--primary-dark));
  border-radius: 4px;
  transition: width 0.3s ease;
}

/* Main Content Layout */
.journey-content {
  display: flex;
  gap: 16px; /* Reduced gap between timeline and details */
  min-height: 600px;
  width: 100% !important;
  max-width: 100% !important;
  overflow-x: hidden !important;
}

/* Timeline Section */
.timeline-section {
  flex: 0 0 320px; /* Reduced width to give more space to details */
  background: var(--surface);
  border-radius: 8px;
  padding: 20px; /* Reduced padding */
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  max-height: 800px;
  overflow-y: auto;
}

.timeline-title {
  font-size: 20px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 24px;
  padding-bottom: 12px;
  border-bottom: 2px solid var(--primary-light);
}

.timeline-item {
  position: relative;
  padding: 16px 0 16px 40px;
  cursor: pointer;
  transition: all 0.3s ease;
  border-radius: 8px;
  margin-bottom: 8px;
}

.timeline-item:hover {
  background: rgba(33, 150, 243, 0.04);
}

.timeline-item.active {
  background: rgba(33, 150, 243, 0.08);
  border-left: 4px solid var(--primary-color);
}

.timeline-item.completed {
  border-left: 4px solid var(--success);
}

.timeline-item.pending {
  border-left: 4px solid var(--divider-color);
}

.timeline-icon {
  position: absolute;
  left: 8px;
  top: 20px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  color: white;
}

.timeline-item.completed .timeline-icon {
  background: var(--success);
}

.timeline-item.pending .timeline-icon {
  background: var(--divider-color);
}

.timeline-item.active .timeline-icon {
  background: var(--primary-color);
}

.timeline-content {
  margin-left: 8px;
}

.timeline-stage {
  font-size: 16px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.timeline-date {
  font-size: 14px;
  color: var(--text-secondary);
  margin-bottom: 8px;
}

.timeline-connector {
  position: absolute;
  left: 19px;
  top: 44px;
  width: 2px;
  height: calc(100% - 24px);
  background: var(--divider-color);
}

.timeline-item:last-child .timeline-connector {
  display: none;
}

/* Details Section */
.details-section {
  flex: 1;
  background: var(--surface);
  border-radius: 8px;
  padding: 24px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  min-height: 600px;
}

.stage-details {
  display: none;
}

.stage-details.active {
  display: block;
}

.details-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--divider-color);
}

.details-title {
  font-size: 24px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.details-subtitle {
  font-size: 16px;
  color: var(--text-secondary);
}

.status-badge {
  padding: 8px 16px;
  border-radius: 20px;
  font-size: 14px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.status-badge.completed {
  background: rgba(76, 175, 80, 0.1);
  color: var(--success);
}

.status-badge.pending {
  background: rgba(255, 152, 0, 0.1);
  color: var(--warning);
}

.status-badge.active {
  background: rgba(33, 150, 243, 0.1);
  color: var(--primary-color);
}

.details-date {
  display: flex;
  align-items: center;
  font-size: 14px;
  color: var(--text-secondary);
  margin-bottom: 16px;
}

.details-date i {
  margin-right: 8px;
  font-size: 16px;
}

.details-content {
  font-size: 16px;
  line-height: 1.6;
  color: var(--text-primary);
  margin-bottom: 24px;
}

/* Files Section */
.files-section {
  background: rgba(33, 150, 243, 0.04);
  border-radius: 8px;
  padding: 20px;
  margin-top: 24px;
  border-left: 4px solid var(--primary-color);
}

.files-section h4 {
  font-size: 18px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 16px;
  display: flex;
  align-items: center;
}

.files-section h4::before {
  content: "📁";
  margin-right: 8px;
}

.file-item {
  display: flex;
  align-items: center;
  padding: 12px;
  background: var(--surface);
  border-radius: 6px;
  margin-bottom: 8px;
  border: 1px solid var(--divider-color);
  transition: all 0.3s ease;
}

.file-item:hover {
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}

.file-icon {
  margin-right: 12px;
  font-size: 20px;
}

.file-info {
  flex: 1;
}

.file-name {
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.file-meta {
  font-size: 12px;
  color: var(--text-secondary);
}

.file-action {
  padding: 8px 16px;
  border-radius: 4px;
  text-decoration: none;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.3s ease;
}

.file-action.download {
  background: var(--primary-color);
  color: white;
}

.file-action.download:hover {
  background: var(--primary-dark);
}

.file-action.view {
  background: var(--success);
  color: white;
}

.file-action.view:hover {
  background: #388E3C;
}

.file-action.disabled {
  background: var(--divider-color);
  color: var(--text-secondary);
  cursor: not-allowed;
}

/* Simple Fixed Dr. Briz Chat Box */
.dr-briz-chat-window {
  position: fixed;
  bottom: 24px;
  right: 24px;
  width: 380px;
  height: 500px;
  background: var(--surface);
  border-radius: 16px;
  box-shadow: 0 12px 48px rgba(0,0,0,0.15), 0 4px 16px rgba(0,0,0,0.1);
  z-index: 1000;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(33, 150, 243, 0.2);
  font-family: 'Roboto', sans-serif;
  overflow: hidden;
  min-width: 320px;
  min-height: 450px;
  max-width: calc(100vw - 48px) !important;
  max-height: calc(100vh - 100px) !important;
}

.dr-briz-chat-window.minimized {
  height: 60px;
  min-height: 60px;
}

.dr-briz-chat-window.minimized #chat-messages,
.dr-briz-chat-window.minimized .chat-input-container {
  display: none;
}

.dr-briz-chat-window.maximized {
  width: calc(100vw - 48px) !important;
  height: calc(100vh - 100px) !important;
  max-width: calc(100vw - 48px) !important;
  max-height: calc(100vh - 100px) !important;
}

.dr-briz-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px;
  background: var(--primary-color);
  color: white;
  border-radius: 16px 16px 0 0;
  position: relative;
  overflow: hidden;
}

.dr-briz-header::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(45deg, rgba(255,255,255,0.1) 0%, transparent 50%, rgba(255,255,255,0.1) 100%);
  pointer-events: none;
}

.dr-briz-header-left {
  display: flex;
  align-items: center;
  gap: 14px;
  position: relative;
  z-index: 1;
}

.dr-briz-header-controls {
  display: flex;
  gap: 6px;
  position: relative;
  z-index: 1;
}

.dr-briz-control-btn {
  background: rgba(255, 255, 255, 0.15);
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: white;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  backdrop-filter: blur(10px);
}

.dr-briz-control-btn:hover {
  background: rgba(255, 255, 255, 0.25);
  border-color: rgba(255, 255, 255, 0.4);
  transform: scale(1.05);
}

.dr-briz-control-btn:active {
  transform: scale(0.95);
}

.dr-briz-header img {
  height: 40px;
  width: 40px;
  object-fit: contain;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  border: 2px solid rgba(255, 255, 255, 0.3);
  transition: all 0.3s ease;
}

.dr-briz-header img:hover {
  transform: scale(1.05);
  box-shadow: 0 6px 16px rgba(0,0,0,0.2);
}

.dr-briz-header span {
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 0.5px;
  text-shadow: 0 1px 2px rgba(0,0,0,0.1);
}

#chat-messages {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
  background: linear-gradient(135deg, #f8f9fa 0%, #ffffff 100%);
  min-height: 200px;
  max-height: 300px;
  scroll-behavior: smooth;
}

#chat-messages::-webkit-scrollbar {
  width: 6px;
}

#chat-messages::-webkit-scrollbar-track {
  background: rgba(0,0,0,0.05);
  border-radius: 3px;
}

#chat-messages::-webkit-scrollbar-thumb {
  background: rgba(33, 150, 243, 0.3);
  border-radius: 3px;
}

#chat-messages::-webkit-scrollbar-thumb:hover {
  background: rgba(33, 150, 243, 0.5);
}

#chat-messages div {
  margin-bottom: 16px;
  padding: 14px 18px;
  border-radius: 12px;
  background: white;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  font-size: 14px;
  line-height: 1.5;
  border: 1px solid rgba(0,0,0,0.05);
  transition: all 0.2s ease;
  position: relative;
}

#chat-messages div:hover {
  box-shadow: 0 4px 12px rgba(0,0,0,0.12);
  transform: translateY(-1px);
}

#chat-messages div b {
  color: var(--primary-color);
  font-weight: 600;
  display: block;
  margin-bottom: 4px;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.chat-input-container {
  display: flex;
  padding: 16px 20px;
  border-top: 1px solid rgba(0,0,0,0.08);
  background: white;
  border-radius: 0 0 16px 16px;
  gap: 12px;
  align-items: center;
}

#chat-input {
  flex: 1;
  border: 2px solid rgba(0,0,0,0.1);
  border-radius: 24px;
  padding: 12px 20px;
  font-size: 14px;
  outline: none;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  background: #f8f9fa;
  font-family: 'Roboto', sans-serif;
}

#chat-input:focus {
  border-color: var(--primary-color);
  background: white;
  box-shadow: 0 0 0 3px rgba(33, 150, 243, 0.1);
  transform: translateY(-1px);
}

#chat-input::placeholder {
  color: #999;
  font-style: italic;
}

#chat-send {
  padding: 12px 24px;
  background: linear-gradient(135deg, var(--primary-color), var(--primary-dark));
  color: white;
  border: none;
  border-radius: 24px;
  font-weight: 600;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 4px 12px rgba(33, 150, 243, 0.3);
  min-width: 80px;
}

#chat-send:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(33, 150, 243, 0.4);
}

#chat-send:active {
  transform: translateY(0);
  box-shadow: 0 2px 8px rgba(33, 150, 243, 0.3);
}

/* File Viewer Modal Styles */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.8);
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.modal-content {
  background: white;
  border-radius: 12px;
  width: 90%;
  max-width: 1200px;
  height: 90%;
  max-height: 800px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid var(--divider-color);
  background: var(--surface);
  border-radius: 12px 12px 0 0;
}

.modal-header h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 20px;
  font-weight: 500;
}

.modal-close {
  background: none;
  border: none;
  font-size: 24px;
  cursor: pointer;
  color: var(--text-secondary);
  padding: 4px;
  border-radius: 4px;
  transition: all 0.2s ease;
}

.modal-close:hover {
  background: rgba(0, 0, 0, 0.1);
  color: var(--text-primary);
}

.modal-body {
  flex: 1;
  padding: 0;
  overflow: hidden;
}

#file-viewer-content {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

#file-viewer-content iframe {
  width: 100%;
  height: 100%;
  border: none;
  border-radius: 0 0 12px 12px;
}

#file-viewer-content img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

#file-viewer-content .pdf-viewer {
  width: 100%;
  height: 100%;
  border: none;
}

        /* Responsive Design */
        @media (max-width: 1200px) {
          .journey-container {
            margin-left: 8px;
            padding: 16px 16px 16px 0;
          }
          
          .journey-content {
            gap: 12px;
          }
          
          .timeline-section {
            flex: 0 0 280px; /* Smaller timeline for more space */
          }
          
          .details-section {
            padding: 16px;
          }
        }

@media (max-width: 1024px) {
  .journey-content {
    flex-direction: column;
  }
  
  .timeline-section {
    flex: none;
    max-height: 350px;
    margin-bottom: 20px;
  }
  
  .details-section {
    min-height: 500px;
  }
  
  .dr-briz-chat-window {
    width: 320px;
    height: 450px;
    bottom: 20px;
    right: 20px;
  }
}

@media (max-width: 900px) {
  .journey-container {
    margin-left: 4px;
    padding: 12px 12px 12px 0;
  }
  
  .journey-header {
    padding: 16px;
  }
  
  .journey-title {
    font-size: 22px;
  }
  
  .timeline-section {
    padding: 16px;
    max-height: 280px;
  }
  
  .details-section {
    padding: 16px;
  }
  
  .dr-briz-chat-window {
    width: 280px;
    height: 380px;
    bottom: 12px;
    right: 12px;
  }
}

@media (max-width: 768px) {
  .journey-container {
    margin-left: 0;
    padding: 12px;
  }
  
  .journey-header {
    padding: 16px;
  }
  
  .journey-title {
    font-size: 22px;
  }
  
  .patient-info {
    font-size: 13px;
  }
  
  .timeline-section {
    padding: 16px;
    max-height: 250px;
  }
  
  .timeline-title {
    font-size: 18px;
    margin-bottom: 16px;
  }
  
  .timeline-stage {
    font-size: 14px;
  }
  
  .timeline-date {
    font-size: 12px;
  }
  
  .details-section {
    padding: 16px;
    min-height: 400px;
  }
  
  .details-title {
    font-size: 20px;
  }
  
  .details-subtitle {
    font-size: 14px;
  }
  
  .dr-briz-chat-window {
    width: calc(100vw - 24px);
    height: 350px;
    right: 12px;
    bottom: 12px;
    min-width: 280px;
  }
  
  .modal-content {
    width: 95%;
    height: 90%;
    padding: 20px;
  }
  
  /* Share Files Modal Responsive */
  #share-files-modal .modal-content {
    width: 95%;
    max-width: 500px;
    max-height: 80vh;
    padding: 16px;
  }
  
  #share-files-list {
    max-height: 200px;
  }
  
  #share-files-form input,
  #share-files-form textarea {
    font-size: 14px;
    padding: 8px;
  }
  
  #share-files-form button {
    padding: 8px 16px;
    font-size: 14px;
  }
  
  .file-item {
    padding: 10px;
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
  
  .file-action {
    align-self: flex-end;
    padding: 6px 12px;
    font-size: 12px;
  }
}

@media (max-width: 480px) {
  .journey-container {
    padding: 8px;
  }
  
  .journey-header {
    padding: 12px;
  }
  
  .journey-title {
    font-size: 20px;
  }
  
  .patient-info {
    font-size: 12px;
  }
  
  .timeline-section {
    padding: 12px;
    max-height: 200px;
  }
  
  .timeline-item {
    padding: 12px 0 12px 32px;
  }
  
  .timeline-icon {
    width: 20px;
    height: 20px;
    left: 6px;
    top: 16px;
  }
  
  .timeline-stage {
    font-size: 13px;
  }
  
  .details-section {
    padding: 12px;
  }
  
  .details-title {
    font-size: 18px;
  }
  
  .dr-briz-chat-window {
    width: calc(100vw - 16px) !important;
    height: 300px !important;
    right: 8px !important;
    bottom: 8px !important;
    max-width: calc(100vw - 16px) !important;
    max-height: calc(100vh - 100px) !important;
  }
  
  .modal-content {
    width: 98%;
    height: 85%;
    padding: 16px;
  }
  
  /* Share Files Modal Responsive for Small Screens */
  #share-files-modal .modal-content {
    width: 98%;
    max-width: none;
    max-height: 85vh;
    padding: 12px;
  }
  
  #share-files-list {
    max-height: 150px;
  }
  
  #share-files-form input,
  #share-files-form textarea {
    font-size: 13px;
    padding: 6px;
  }
  
  #share-files-form button {
    padding: 6px 12px;
    font-size: 13px;
  }
  
  #share-files-form label {
    font-size: 13px;
  }
}
</style>



<div class="journey-container">
  <!-- Header -->
  <div class="journey-header">
    <div class="journey-title">Patient Journey</div>
    <div class="patient-info">
      Patient: {{ patient.name }} • ID: {{ patient.id }} 
      {% if patient.gender %}• Gender: {{ patient.gender }}{% endif %}
      {% if age %}• Age: {{ age }}{% endif %}
      {% if patient.clinic %}• Clinic: {{ patient.clinic.name }}{% endif %}
      • Treatment Progress
    </div>
    <div class="progress-section">
      <div class="progress-label">Overall Progress</div>
      <div class="progress-text">{{ completed_stages }} of {{ total_stages }} steps completed ({{ progress_percentage }}%)</div>
      <div class="progress-bar">
        <div class="progress-fill" style="width: {{ progress_percentage }}%"></div>
      </div>
    </div>
  </div>

  <!-- AI Guidance Section -->
  {% if ai_guidance %}
  <div class="ai-guidance-section" style="background: var(--surface); border-radius: 8px; padding: 20px; margin-bottom: 24px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-left: 4px solid var(--primary-color);">
    <div style="display: flex; align-items: center; margin-bottom: 16px;">
      <div style="background: var(--primary-color); border-radius: 50%; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; margin-right: 12px;">
        <i class="material-icons" style="font-size: 20px; color: white;">psychology</i>
      </div>
      <div>
        <h3 style="margin: 0; font-size: 18px; font-weight: 500; color: var(--text-primary);">AI Workflow Assistant</h3>
        <div style="font-size: 13px; color: var(--text-secondary);">{{ ai_guidance.status_summary }}</div>
      </div>
    </div>
    
    <!-- Progress Overview -->
    <div style="display: flex; align-items: center; margin-bottom: 16px; background: var(--background); border-radius: 6px; padding: 12px; border: 1px solid var(--divider-color);">
      <div style="flex: 1;">
        <div style="font-size: 14px; font-weight: 500; margin-bottom: 4px; color: var(--text-primary);">Progress: {{ ai_guidance.completed_count }} of {{ ai_guidance.total_stages }} stages</div>
        <div style="background: var(--divider-color); border-radius: 10px; height: 8px; overflow: hidden;">
          <div style="background: var(--success); height: 100%; width: {{ ai_guidance.progress_percentage }}%; transition: width 0.3s ease;"></div>
        </div>
      </div>
      <div style="margin-left: 16px; text-align: right;">
        <div style="font-size: 18px; font-weight: 600; color: var(--text-primary);">{{ "%.0f"|format(ai_guidance.progress_percentage) }}%</div>
      </div>
    </div>
    
        <!-- Current Stage and Next Actions -->
    {% if ai_guidance.current_stage %}
    <div style="margin-bottom: 16px;">
      <div style="font-size: 14px; font-weight: 500; margin-bottom: 8px; color: var(--text-primary);">🎯 Current Focus: Stage {{ ai_guidance.current_stage|replace('_', ' ')|title }}</div>
      
      {% if ai_guidance.next_actions %}
      <div style="background: var(--background); border-radius: 6px; padding: 12px; margin-top: 8px; border: 1px solid var(--divider-color);">
        <div style="font-size: 13px; font-weight: 500; margin-bottom: 6px; color: var(--text-primary);">💡 Recommended Next Actions:</div>
        {% for action in ai_guidance.next_actions %}
        <div style="font-size: 12px; margin-bottom: 8px; padding: 8px; background: var(--surface); border-radius: 4px; border: 1px solid var(--divider-color);">
          <strong style="color: var(--text-primary);">{{ action.description }}</strong><br>
          <span style="color: var(--text-secondary); font-size: 11px;">{{ action.reason }}</span>
          <div style="margin-top: 6px;">
            {% if action.action == 'schedule_consultation' %}
              <button onclick="openScheduleModal('{{ ai_guidance.current_stage }}', '{{ action.action_key }}', 'Schedule {{ ai_guidance.current_stage|replace('_', ' ')|title }}')" 
                      style="font-size: 10px; padding: 4px 8px; background: var(--success); color: white; border: none; border-radius: 3px; cursor: pointer; margin-right: 4px;">
                📅 Schedule Now
              </button>
            {% elif action.action == 'update_consultation' %}
              <button onclick="openUpdateModal('{{ ai_guidance.current_stage }}', '{{ action.action_key }}', 'Update {{ ai_guidance.current_stage|replace('_', ' ')|title }}', '{{ stage_existing_data.get(ai_guidance.current_stage, {}).get('id', '') }}')" 
                      style="font-size: 10px; padding: 4px 8px; background: var(--warning); color: white; border: none; border-radius: 3px; cursor: pointer; margin-right: 4px;">
                🔄 Update Now
              </button>
            {% elif action.action == 'confirm_completion' %}
              <button onclick="executeStageAction('{{ ai_guidance.current_stage }}', 'confirm', '{{ action.action_key }}')" 
                      style="font-size: 10px; padding: 4px 8px; background: var(--primary-color); color: white; border: none; border-radius: 3px; cursor: pointer; margin-right: 4px;">
                ✅ Confirm Complete
              </button>
            {% endif %}
            <button onclick="askWorkflowQuestion('Tell me more about {{ ai_guidance.current_stage|replace('_', ' ') }} and why it is important')" 
                    style="font-size: 10px; padding: 4px 8px; background: var(--divider-color); color: var(--text-primary); border: none; border-radius: 3px; cursor: pointer;">
                ℹ️ Learn More
            </button>
          </div>
        </div>
        {% endfor %}
      </div>
      {% endif %}
    </div>
    {% endif %}
    
    <!-- AI Recommendations -->
    {% if ai_guidance.recommendations %}
    <div>
      <div style="font-size: 14px; font-weight: 500; margin-bottom: 8px; color: var(--text-primary);">🤖 AI Insights:</div>
      {% for rec in ai_guidance.recommendations %}
      <div style="background: var(--surface); border-radius: 6px; padding: 10px; margin-bottom: 8px; border-left: 3px solid {% if rec.priority == 'high' %}var(--error){% elif rec.priority == 'medium' %}var(--warning){% else %}var(--success){% endif %}; border: 1px solid var(--divider-color);">
        <div style="font-size: 12px; font-weight: 500; margin-bottom: 4px; color: var(--text-primary);">
          {% if rec.type == 'start' %}🚀 Start Treatment
          {% elif rec.type == 'next_step' %}➡️ Next Step
          {% elif rec.type == 'context' %}ℹ️ Context
          {% else %}💡 Recommendation
          {% endif %}
        </div>
        <div style="font-size: 12px; color: var(--text-secondary);">{{ rec.message }}</div>
        <div style="margin-top: 6px;">
          {% if rec.type == 'start' %}
            <button onclick="askWorkflowQuestion('How do I start the treatment process?')" 
                    style="font-size: 10px; padding: 4px 8px; background: var(--success); color: white; border: none; border-radius: 3px; cursor: pointer; margin-right: 4px;">
              🚀 Start Now
            </button>
          {% elif rec.type == 'next_step' %}
            <button onclick="askWorkflowQuestion('What is the next step and how do I complete it?')" 
                    style="font-size: 10px; padding: 4px 8px; background: var(--primary-color); color: white; border: none; border-radius: 3px; cursor: pointer; margin-right: 4px;">
              ➡️ Next Step
            </button>
          {% elif rec.type == 'context' %}
            <button onclick="askWorkflowQuestion('Tell me more about this stage and its importance')" 
                    style="font-size: 10px; padding: 4px 8px; background: var(--warning); color: white; border: none; border-radius: 3px; cursor: pointer; margin-right: 4px;">
              ℹ️ Learn More
            </button>
          {% endif %}
          <button onclick="askWorkflowQuestion('{{ rec.message }}')" 
                  style="font-size: 10px; padding: 4px 8px; background: var(--divider-color); color: var(--text-primary); border: none; border-radius: 3px; cursor: pointer;">
              💬 Ask Dr. Briz
          </button>
        </div>
      </div>
      {% endfor %}
    </div>
    {% endif %}
  </div>
  {% endif %}

  <!-- Main Content -->
  <div class="journey-content">
    <!-- Timeline Section -->
    <div class="timeline-section">
      <div class="timeline-title">Treatment Timeline</div>
      {% for stage in stages %}
      <div class="timeline-item {% if stage.status == 'completed' %}completed{% elif stage.status == 'active' %}active{% else %}pending{% endif %}" 
           onclick="selectStage('{{ stage.key }}')">
        <div class="timeline-icon">
          {% if stage.status == 'completed' %}
            <i class="material-icons" style="font-size: 14px;">check</i>
          {% elif stage.status == 'active' %}
            <i class="material-icons" style="font-size: 14px;">radio_button_checked</i>
          {% else %}
            <i class="material-icons" style="font-size: 14px;">radio_button_unchecked</i>
          {% endif %}
        </div>
        <div class="timeline-content">
          <div class="timeline-stage">{{ stage.name }}</div>
          {% if stage.status == 'completed' and stage.date %}
          <div class="timeline-date">{{ stage.date }}</div>
          {% endif %}
          {% if stage.files %}
          <div style="font-size: 12px; color: var(--primary-color); margin-top: 4px;">
            📁 {{ stage.files|length }} file(s)
          </div>
          {% endif %}
        </div>
        <div class="timeline-connector"></div>
      </div>
      {% endfor %}
    </div>

    <!-- Details Section -->
    <div class="details-section">
      {% for stage in stages %}
      <div class="stage-details" id="details-{{ stage.key }}" data-stage-key="{{ stage.key }}">
        <div class="details-header">
          <div>
            <div class="details-title">{{ stage.name }}</div>
            <div class="details-subtitle">{{ stage.subtitle or 'Treatment Stage' }}</div>
          </div>
          <div class="status-badge {{ stage.status }}">
            {{ stage.status|title }}
          </div>
        </div>
        
        {% if stage.status == 'completed' and stage.date %}
        <div class="details-date">
          <i class="material-icons">event</i>
          Date: {{ stage.date }}
        </div>
        {% endif %}
        
        <div class="details-content">
          {{ stage.description or 'No description available for this stage.' }}
        </div>
        
        <!-- Existing Data Display -->
        {% set existing_data = stage_existing_data.get(stage.key) %}
        {% if existing_data %}
        <div class="existing-data-section" style="margin-top: 16px; padding: 12px; background: #e8f5e8; border-radius: 6px; border-left: 4px solid #4CAF50;">
          <h6 style="margin: 0 0 8px 0; color: #2E7D32; font-size: 13px; font-weight: 600;">📋 Existing Data</h6>
          
          {% if existing_data.type == 'consultation' %}
            <div style="font-size: 12px; color: #2E7D32;">
              <strong>Scheduled:</strong> {{ existing_data.scheduled_date.strftime('%B %d, %Y at %I:%M %p') if existing_data.scheduled_date else 'Not specified' }}<br>
              {% if existing_data.doctor_name %}
              <strong>Doctor:</strong> {{ existing_data.doctor_name }}<br>
              {% endif %}
              {% if existing_data.notes %}
              <strong>Notes:</strong> {{ existing_data.notes }}<br>
              {% endif %}
              <strong>Status:</strong> <span style="color: {% if existing_data.status == 'completed' %}#4CAF50{% elif existing_data.status == 'scheduled' %}#FF9800{% else %}#666{% endif %}; font-weight: 500;">{{ existing_data.status|title }}</span>
            </div>
          {% elif existing_data.type == 'consultation_completed' %}
            <div style="font-size: 12px; color: #2E7D32;">
              <strong>Completed:</strong> {{ existing_data.completed_date.strftime('%B %d, %Y at %I:%M %p') if existing_data.completed_date else 'Not specified' }}<br>
              {% if existing_data.comment %}
              <strong>Comment:</strong> {{ existing_data.comment }}<br>
              {% endif %}
              {% if existing_data.scheduled_date %}
              <strong>Originally Scheduled:</strong> {{ existing_data.scheduled_date.strftime('%B %d, %Y') }}
              {% endif %}
            </div>
          {% elif existing_data.type == 'file' or existing_data.type == 'admin_file' %}
            <div style="font-size: 12px; color: #2E7D32;">
              <strong>File:</strong> {{ existing_data.file_name }}<br>
              <strong>Uploaded:</strong> {{ existing_data.upload_date.strftime('%B %d, %Y') if existing_data.upload_date else 'Not specified' }}<br>
              <strong>Type:</strong> {{ existing_data.file_type|upper }}<br>
              {% if existing_data.notes %}
              <strong>Notes:</strong> {{ existing_data.notes }}
              {% endif %}
            </div>
          {% elif existing_data.type == 'device_order' %}
            <div style="font-size: 12px; color: #2E7D32;">
              <strong>Device:</strong> {{ existing_data.device_name }}<br>
              <strong>Ordered:</strong> {{ existing_data.order_date.strftime('%B %d, %Y') if existing_data.order_date else 'Not specified' }}<br>
              <strong>Status:</strong> <span style="color: {% if existing_data.status == 'ordered' %}#FF9800{% elif existing_data.status == 'delivered' %}#4CAF50{% else %}#666{% endif %}; font-weight: 500;">{{ existing_data.status|title }}</span><br>
              {% if existing_data.notes %}
              <strong>Notes:</strong> {{ existing_data.notes }}
              {% endif %}
            </div>
          {% elif existing_data.type == 'device_delivered' %}
            <div style="font-size: 12px; color: #2E7D32;">
              <strong>Device:</strong> {{ existing_data.device_name }}<br>
              <strong>Delivered:</strong> {{ existing_data.arrival_date.strftime('%B %d, %Y') if existing_data.arrival_date else 'Not specified' }}<br>
              <strong>Status:</strong> <span style="color: #4CAF50; font-weight: 500;">{{ existing_data.status|title }}</span><br>
              {% if existing_data.notes %}
              <strong>Notes:</strong> {{ existing_data.notes }}
              {% endif %}
            </div>
          {% elif existing_data.type == 'quiz' %}
            <div style="font-size: 12px; color: #2E7D32;">
              <strong>Quiz Type:</strong> {{ existing_data.quiz_type|title }}<br>
              <strong>Completed:</strong> {{ existing_data.date.strftime('%B %d, %Y') if existing_data.date else 'Not specified' }}<br>
              {% if existing_data.patient_email %}
              <strong>Email:</strong> {{ existing_data.patient_email }}
              {% endif %}
            </div>
          {% endif %}
        </div>
        {% endif %}
        
        {% if stage.files %}
        <div class="files-section">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
            <h4>Associated Files ({{ stage.files|length }})</h4>
            <button onclick="openShareFilesModal('{{ stage.key }}', '{{ stage.name }}')" 
                    class="share-files-btn" 
                    style="background: var(--primary-color); color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 14px; display: flex; align-items: center; gap: 6px;">
              📤 Share Files
            </button>
          </div>
          {% for file in stage.files %}
          <div class="file-item" data-file-id="{{ file.id }}" data-file-name="{{ file.name }}" data-file-s3-key="{{ file.s3_key }}">
            <div class="file-icon">📄</div>
            <div class="file-info">
              <div class="file-name">{{ file.name }}</div>
              <div class="file-meta">{{ file.date.strftime('%B %d, %Y') }} - {{ file.description }}</div>
            </div>
            {% if file.download_url %}
              {% if file.is_viewable %}
              <button onclick="viewFile('{{ file.download_url }}', '{{ file.name }}', '{{ file.file_type }}')" class="file-action view">
                👁️ View
              </button>
              {% else %}
              <a href="{{ file.download_url }}" target="_blank" class="file-action download">
                📥 Download
              </a>
              {% endif %}
            {% else %}
            <button class="file-action disabled" disabled>
              No Link
            </button>
            {% endif %}
          </div>
          {% endfor %}
        </div>
        {% endif %}
        
        <!-- Stage Actions Section -->
        {% if stage_actions and stage_actions.get(stage.key) %}
        <div class="actions-section" style="margin-top: 20px; padding: 16px; background: #f8f9fa; border-radius: 8px; border: 1px solid #e9ecef;">
          <h4 style="margin: 0 0 12px 0; color: #495057; font-size: 16px;">🎯 Stage Actions</h4>
          
          {% set stage_action_data = stage_actions.get(stage.key) %}
          
          <!-- Schedule Actions -->
          {% if stage_action_data and stage_action_data.schedule %}
          <div class="schedule-actions">
            <h5 style="margin: 0 0 8px 0; color: #6c757d; font-size: 14px; font-weight: 600;">📅 Schedule Actions</h5>
            <div style="display: flex; gap: 8px; flex-wrap: wrap;">
              {% for action in stage_action_data.schedule %}
                {% set existing_data = stage_existing_data.get(stage.key) %}
                {% if existing_data and (existing_data.type == 'consultation' or existing_data.type == 'consultation_completed') %}
                  <!-- Show Update button when consultation already exists -->
                  <button onclick="openUpdateModal('{{ stage.key }}', '{{ action.key }}', '{{ action.display_name }}', '{{ existing_data.id }}')" 
                          class="action-btn update-btn" 
                          style="background: #FF9800; color: white; border: none; padding: 8px 12px; border-radius: 4px; cursor: pointer; font-size: 13px; display: flex; align-items: center; gap: 4px;">
                    🔄 Update {{ action.display_name }}
                  </button>
                {% else %}
                  <!-- Show Schedule button when no consultation exists -->
                  <button onclick="openScheduleModal('{{ stage.key }}', '{{ action.key }}', '{{ action.display_name }}')" 
                          class="action-btn schedule-btn" 
                          style="background: #007bff; color: white; border: none; padding: 8px 12px; border-radius: 4px; cursor: pointer; font-size: 13px; display: flex; align-items: center; gap: 4px;">
                    📅 {{ action.display_name }}
                  </button>
                {% endif %}
              {% endfor %}
            </div>
          </div>
          {% endif %}
          
          <!-- Confirm Actions -->
          {% if stage_action_data and stage_action_data.confirm %}
          <div class="confirm-actions" style="margin-top: 12px;">
            <h5 style="margin: 0 0 8px 0; color: #6c757d; font-size: 14px; font-weight: 600;">✅ Confirm Actions</h5>
            <div style="display: flex; gap: 8px; flex-wrap: wrap;">
              {% for action in stage_action_data.confirm %}
              <button onclick="executeStageAction('{{ stage.key }}', 'confirm', '{{ action.key }}')" 
                      class="action-btn confirm-btn" 
                      style="background: #28a745; color: white; border: none; padding: 8px 12px; border-radius: 4px; cursor: pointer; font-size: 13px; display: flex; align-items: center; gap: 4px;">
                ✅ {{ action.display_name }}
              </button>
              {% endfor %}
            </div>
          </div>
          {% endif %}
          
          <!-- Manual Completion Actions -->

        </div>
        {% endif %}
      </div>
      {% endfor %}
    </div>
  </div>
  
  <!-- Testing Links Section (Bottom) -->
  <div style="margin-top: 40px; padding: 20px; background: #f5f5f5; border-radius: 8px; border: 1px solid #ddd;">
    <h4 style="margin: 0 0 15px 0; color: #666; font-size: 14px;">🔧 Testing Links (Development Only)</h4>
    <div style="display: flex; gap: 15px; flex-wrap: wrap;">
      <button onclick="toggleManifest('validation')" 
              style="padding: 8px 12px; background: #2196F3; color: white; border: none; border-radius: 4px; font-size: 12px; cursor: pointer;">
         📋 Validation Manifest
      </button>
      <button onclick="toggleManifest('clinical')" 
              style="padding: 8px 12px; background: #4CAF50; color: white; border: none; border-radius: 4px; font-size: 12px; cursor: pointer;">
         🏥 Clinical Manifest
      </button>
      <button onclick="toggleManifest('both')" 
              style="padding: 8px 12px; background: #FF9800; color: white; border: none; border-radius: 4px; font-size: 12px; cursor: pointer;">
         🔄 Both Manifests
      </button>
    </div>
    <p style="margin: 10px 0 0 0; font-size: 11px; color: #888;">
      Click these buttons to show/hide different manifest views for testing purposes.
    </p>
    
    <!-- Debug section for stages_json -->
    <div style="margin-top: 20px; padding: 15px; background: #fff3cd; border-radius: 4px; border: 1px solid #ffeaa7;">
      <h5 style="margin: 0 0 10px 0; color: #856404; font-size: 13px;">🐛 Debug: Stages JSON Data</h5>
      <div style="font-family: monospace; font-size: 11px; background: white; padding: 10px; border-radius: 3px; max-height: 200px; overflow-y: auto;">
        <strong>Raw stages_json:</strong><br>
        <pre style="margin: 5px 0; white-space: pre-wrap;">{{ stages_json|default('NO DATA') }}</pre>
        <strong>Length:</strong> {{ stages_json|length if stages_json else 0 }}<br>
        <strong>Type:</strong> {{ stages_json.__class__.__name__ if stages_json else 'None' }}
      </div>
    </div>
  </div>
  
  <!-- Collapsible Manifest Information Display -->
  <div id="validation-manifest" class="manifest-section" style="display: none; margin-top: 20px; padding: 20px; background: #e3f2fd; border-radius: 8px; border: 1px solid #2196F3;">
    <h4 style="margin: 0 0 15px 0; color: #1565C0; font-size: 16px;">📋 Validation Manifest Data</h4>
    <div style="padding: 10px; background: white; border-radius: 4px; font-family: monospace; font-size: 12px; max-height: 300px; overflow-y: auto;">
      <pre style="margin: 0;">{{ patient_manifest|tojson(indent=2) }}</pre>
    </div>
  </div>

  <div id="clinical-manifest" class="manifest-section" style="display: none; margin-top: 20px; padding: 20px; background: #e8f5e8; border-radius: 8px; border: 1px solid #4CAF50;">
    <h4 style="margin: 0 0 15px 0; color: #2E7D32; font-size: 16px;">🏥 Document-Based Clinical Observations</h4>
    {% if document_observations %}
      {% for source_type, observations in document_observations.items() %}
      <div style="margin-bottom: 15px; padding: 10px; background: white; border-radius: 4px; border-left: 4px solid #4CAF50;">
        <h6 style="margin: 0 0 8px 0; color: #2E7D32; font-size: 13px;">{{ source_type.upper() }} Documents ({{ observations|length }} observations):</h6>
        {% for obs in observations[:5] %}
        <div style="margin-bottom: 8px; padding: 8px; background: #f9f9f9; border-radius: 3px;">
          <strong>{{ obs.observation }}:</strong> {{ obs.value }}
          {% if obs.document_name %}
          <br><small style="color: #666;">Source: {{ obs.document_name }}</small>
          {% endif %}
          {% if obs.evidence %}
          <br><small style="color: #666;">Evidence: {{ obs.evidence[:100] }}{% if obs.evidence|length > 100 %}...{% endif %}</small>
          {% endif %}
        </div>
        {% endfor %}
        {% if observations|length > 5 %}
        <div style="font-size: 12px; color: #666; font-style: italic;">
          ... and {{ observations|length - 5 }} more observations
        </div>
        {% endif %}
      </div>
      {% endfor %}
    {% else %}
      <div style="padding: 10px; background: #fff3cd; border-radius: 4px; border: 1px solid #ffeaa7;">
        <p style="margin: 0; color: #856404; font-size: 13px;">⚠️ No document-based clinical observations found for this patient.</p>
      </div>
    {% endif %}
  </div>

  <div id="both-manifest" class="manifest-section" style="display: none; margin-top: 20px; padding: 20px; background: #fff3e0; border-radius: 8px; border: 1px solid #FF9800;">
    <h4 style="margin: 0 0 15px 0; color: #E65100; font-size: 16px;">🔄 Both Manifests View</h4>
    
    <!-- Clinical Observations Section -->
    <div style="margin-bottom: 20px;">
      <h5 style="margin: 0 0 10px 0; color: #E65100; font-size: 14px;">🏥 Document-Based Clinical Observations:</h5>
      {% if document_observations %}
        {% for source_type, observations in document_observations.items() %}
        <div style="margin-bottom: 15px; padding: 10px; background: white; border-radius: 4px; border-left: 4px solid #FF9800;">
          <h6 style="margin: 0 0 8px 0; color: #E65100; font-size: 13px;">{{ source_type.upper() }} Documents ({{ observations|length }} observations):</h6>
          {% for obs in observations[:3] %}
          <div style="margin-bottom: 8px; padding: 8px; background: #f9f9f9; border-radius: 3px;">
            <strong>{{ obs.observation }}:</strong> {{ obs.value }}
            {% if obs.document_name %}
            <br><small style="color: #666;">Source: {{ obs.document_name }}</small>
            {% endif %}
          </div>
          {% endfor %}
          {% if observations|length > 3 %}
          <div style="font-size: 12px; color: #666; font-style: italic;">
            ... and {{ observations|length - 3 }} more observations
          </div>
          {% endif %}
        </div>
        {% endfor %}
      {% else %}
        <div style="padding: 10px; background: #fff3cd; border-radius: 4px; border: 1px solid #ffeaa7;">
          <p style="margin: 0; color: #856404; font-size: 13px;">⚠️ No document-based clinical observations found for this patient.</p>
        </div>
      {% endif %}
    </div>
    
    <!-- Validation Manifest Section -->
    <div>
      <h5 style="margin: 0 0 10px 0; color: #E65100; font-size: 14px;">📋 Validation Manifest Data:</h5>
      <div style="padding: 10px; background: white; border-radius: 4px; font-family: monospace; font-size: 12px; max-height: 200px; overflow-y: auto;">
        <pre style="margin: 0;">{{ patient_manifest|tojson(indent=2) }}</pre>
      </div>
    </div>
  </div>

  <script>
  function toggleManifest(manifestType) {
    // Hide all manifest sections first
    const allSections = document.querySelectorAll('.manifest-section');
    allSections.forEach(section => {
      section.style.display = 'none';
    });
    
    // Show the selected section
    const selectedSection = document.getElementById(manifestType + '-manifest');
    if (selectedSection) {
      selectedSection.style.display = 'block';
      
      // Scroll to the section
      selectedSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }
  </script>
</div>

<!-- File Viewer Modal -->
<div id="file-viewer-modal" class="modal-overlay" style="display: none;">
  <div class="modal-content">
    <div class="modal-header">
      <h3 id="modal-title">File Viewer</h3>
      <button class="modal-close" onclick="closeFileViewer()">&times;</button>
    </div>
    <div class="modal-body">
      <div id="file-viewer-content">
        <!-- File content will be loaded here -->
      </div>
    </div>
  </div>
</div>

<!-- Share Files Modal -->
<div id="share-files-modal" class="modal-overlay" style="display: none;">
  <div class="modal-content" style="max-width: 600px; max-height: 80vh;">
    <div class="modal-header">
      <h3 id="share-modal-title">Share Files</h3>
      <button class="modal-close" onclick="closeShareFilesModal()">&times;</button>
    </div>
    <div class="modal-body" style="padding: 20px;">
      <div id="share-stage-info" style="margin-bottom: 20px; padding: 10px; background: #f5f5f5; border-radius: 4px;">
        <strong>Stage:</strong> <span id="share-stage-name"></span>
      </div>
      
      <div id="share-files-selection" style="margin-bottom: 20px;">
        <h4 style="margin-bottom: 10px;">Select Files to Share:</h4>
        <div id="share-files-list">
          <!-- Files will be populated here -->
        </div>
      </div>
      
      <form id="share-files-form">
        <div style="margin-bottom: 15px;">
          <label for="recipient-email" style="display: block; margin-bottom: 5px; font-weight: 500;">Recipient Emails (separate multiple emails with commas):</label>
          <input type="text" id="recipient-email" name="email" required 
                 placeholder="email1@example.com, email2@example.com"
                 style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;">
        </div>
        
        <div style="margin-bottom: 20px;">
          <label for="email-message" style="display: block; margin-bottom: 5px; font-weight: 500;">Message:</label>
          <textarea id="email-message" name="message" rows="4" 
                    style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; resize: vertical;">Please find the selected files from the patient journey below.</textarea>
        </div>
        
        <div style="display: flex; gap: 10px; justify-content: flex-end;">
          <button type="button" onclick="closeShareFilesModal()" 
                  style="padding: 10px 20px; background: #666; color: white; border: none; border-radius: 4px; cursor: pointer;">
            Cancel
          </button>
          <button type="submit" id="send-files-btn"
                  style="padding: 10px 20px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; transition: all 0.3s ease;">
            Send Files
          </button>
        </div>
      </form>
    </div>
  </div>
</div>

<!-- Schedule Consultation Modal -->
<div id="schedule-modal" class="modal-overlay" style="display: none;">
  <div class="modal-content" style="max-width: 500px;">
    <div class="modal-header">
      <h3 id="schedule-modal-title">Schedule Consultation</h3>
      <button class="modal-close" onclick="closeScheduleModal()">&times;</button>
    </div>
    <div class="modal-body" style="padding: 20px;">
      <div id="schedule-action-info" style="margin-bottom: 20px; padding: 10px; background: #f5f5f5; border-radius: 4px;">
        <strong>Action:</strong> <span id="schedule-action-name"></span>
      </div>
      
      <form id="schedule-form">
        <input type="hidden" id="schedule-stage-key" name="stage_key">
        <input type="hidden" id="schedule-action-key" name="action_key">
        
        <div style="margin-bottom: 15px;">
          <label for="consult-date" style="display: block; margin-bottom: 5px; font-weight: 500;">Date:</label>
          <input type="date" id="consult-date" name="consult_date" 
                 style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;" required>
        </div>
        
        <div style="margin-bottom: 15px;">
          <label for="consult-time" style="display: block; margin-bottom: 5px; font-weight: 500;">Time:</label>
          <input type="time" id="consult-time" name="consult_time" 
                 style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;" required>
        </div>
        
        <div style="margin-bottom: 15px;">
          <label for="doctor-name" style="display: block; margin-bottom: 5px; font-weight: 500;">Doctor/Provider Name:</label>
          <input type="text" id="doctor-name" name="doctor_name" placeholder="Dr. Smith" 
                 style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;" required>
        </div>
        
        <div style="margin-bottom: 20px;">
          <label for="consult-notes" style="display: block; margin-bottom: 5px; font-weight: 500;">Notes (optional):</label>
          <textarea id="consult-notes" name="consult_notes" rows="3" placeholder="Additional notes about the consultation..." 
                    style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; resize: vertical;"></textarea>
        </div>
        
        <div style="display: flex; gap: 10px; justify-content: flex-end;">
          <button type="button" onclick="closeScheduleModal()" 
                  style="padding: 10px 20px; background: #666; color: white; border: none; border-radius: 4px; cursor: pointer;">
            Cancel
          </button>
          <button type="submit" id="schedule-btn"
                  style="padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; transition: all 0.3s ease;">
            Schedule Consultation
          </button>
        </div>
      </form>
    </div>
  </div>
</div>

<!-- Simple Fixed Dr. Briz Chat Box -->
<div id="dr-briz-chat" class="dr-briz-chat-window">
  <div class="dr-briz-header">
    <div class="dr-briz-header-left">
      <img src="/flask_static/branding/drbriz_logo.png" alt="Dr. Briz Logo" />
      <span>Dr. Briz</span>
    </div>
    <div class="dr-briz-header-controls">
      <button class="dr-briz-control-btn" id="minimize-btn" title="Minimize">−</button>
      <button class="dr-briz-control-btn" id="maximize-btn" title="Maximize">□</button>
    </div>
  </div>
  <div id="chat-messages">
    <div><b>Dr. Briz:</b> Welcome! I am Dr. Briz, your OSA Expert. I can provide both operational and clinical insights regarding your patient. I can also guide you through the treatment workflow - just ask me about next steps, current progress, or any specific stage!</div>
  </div>
  <div class="chat-input-container">
    <input type="text" id="chat-input" placeholder="Type your message..." />
    <button id="chat-send">Send</button>
  </div>
  
  <!-- Quick Workflow Guidance Buttons -->
  <div class="workflow-guidance-buttons" style="padding: 8px; background: var(--background); border-top: 1px solid var(--divider-color);">
    <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 6px;">Quick Guidance:</div>
    <div style="display: flex; gap: 4px; flex-wrap: wrap;">
      <button onclick="askWorkflowQuestion('What should I do next?')" style="font-size: 10px; padding: 4px 8px; background: var(--primary-color); color: white; border: none; border-radius: 3px; cursor: pointer;">Next Steps</button>
      <button onclick="askWorkflowQuestion('What stage am I on and what does it involve?')" style="font-size: 10px; padding: 4px 8px; background: var(--primary-color); color: white; border: none; border-radius: 3px; cursor: pointer;">Current Stage</button>
      <button onclick="askWorkflowQuestion('How is my progress and what phase am I in?')" style="font-size: 10px; padding: 4px 8px; background: var(--primary-color); color: white; border: none; border-radius: 3px; cursor: pointer;">Progress</button>
      <button onclick="askWorkflowQuestion('What is the next stage and why is it important?')" style="font-size: 10px; padding: 4px 8px; background: var(--primary-color); color: white; border: none; border-radius: 3px; cursor: pointer;">Next Stage</button>
    </div>
  </div>
</div>

<script>
// Stage selection functionality
function selectStage(stageKey) {
  // Update active stage in timeline
  document.querySelectorAll('.timeline-item').forEach(item => {
    item.classList.remove('active');
  });
  
  // Find and activate the selected stage
  const selectedItem = document.querySelector(`[onclick="selectStage('${stageKey}')"]`);
  if (selectedItem) {
    selectedItem.classList.add('active');
  }
  
  // Update details section
  document.querySelectorAll('.stage-details').forEach(detail => {
    detail.classList.remove('active');
  });
  
  const selectedDetail = document.getElementById('details-' + stageKey);
  if (selectedDetail) {
    selectedDetail.classList.add('active');
  }
}

// Chat functionality
document.getElementById('chat-send').addEventListener('click', sendChatMessage);
document.getElementById('chat-input').addEventListener('keypress', function(e) {
  if (e.key === 'Enter') {
    sendChatMessage();
  }
});

function sendChatMessage() {
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  
  if (!message) return;
  
  const messagesDiv = document.getElementById('chat-messages');
  messagesDiv.innerHTML += `<div><b>You:</b> ${message}</div>`;
  input.value = '';
  
  // Send to backend
  fetch('/api/bedrock_chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      patient_id: {{ patient.id }},
      message: message
    })
  })
  .then(response => response.json())
  .then(data => {
    if (data.success) {
      messagesDiv.innerHTML += `<div><b>Dr. Briz:</b> ${data.response}</div>`;
    } else {
      // Handle error responses with user-friendly messages
      const errorMessage = data.response || data.message || 'An error occurred';
      messagesDiv.innerHTML += `<div style="color: #d32f2f; font-style: italic;"><b>Dr. Briz:</b> ${errorMessage}</div>`;
    }
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
  })
  .catch(error => {
    console.error('Error:', error);
    messagesDiv.innerHTML += `<div style="color: #d32f2f; font-style: italic;"><b>Dr. Briz:</b> ⚠️ Network connection issue. Please check your internet and try again.</div>`;
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
  });
}

function askWorkflowQuestion(question) {
  const messagesDiv = document.getElementById('chat-messages');
  messagesDiv.innerHTML += `<div><b>You:</b> ${question}</div>`;
  
  // Send to backend
  fetch('/api/bedrock_chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      patient_id: {{ patient.id }},
      message: question
    })
  })
  .then(response => response.json())
  .then(data => {
    if (data.success) {
      messagesDiv.innerHTML += `<div><b>Dr. Briz:</b> ${data.response}</div>`;
    } else {
      // Handle error responses with user-friendly messages
      const errorMessage = data.response || data.message || 'An error occurred';
      messagesDiv.innerHTML += `<div style="color: #d32f2f; font-style: italic;"><b>Dr. Briz:</b> ${errorMessage}</div>`;
    }
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
  })
  .catch(error => {
    console.error('Error:', error);
    messagesDiv.innerHTML += `<div style="color: #d32f2f; font-style: italic;"><b>Dr. Briz:</b> ⚠️ Network connection issue. Please check your internet and try again.</div>`;
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
  });
}

// Simple Minimize/Maximize functionality
const chatWindow = document.getElementById('dr-briz-chat');

document.getElementById('minimize-btn').addEventListener('click', function() {
  chatWindow.classList.toggle('minimized');
});

document.getElementById('maximize-btn').addEventListener('click', function() {
  chatWindow.classList.toggle('maximized');
});

// File Viewer Functions
function viewFile(fileUrl, fileName, fileType) {
  const modal = document.getElementById('file-viewer-modal');
  const modalTitle = document.getElementById('modal-title');
  const content = document.getElementById('file-viewer-content');
  
  modalTitle.textContent = fileName;
  modal.style.display = 'flex';
  
  // Clear previous content
  content.innerHTML = '';
  
  // Determine how to display the file based on type
  console.log('viewFile called with:', { fileUrl, fileName, fileType });
  
  // Handle different file type formats
  let fileExtension = '';
  if (fileType) {
    const fileTypeLower = fileType.toLowerCase().trim();
    
    // If it's a MIME type (e.g., "application/pdf")
    if (fileTypeLower.includes('/')) {
      if (fileTypeLower.includes('pdf')) fileExtension = 'pdf';
      else if (fileTypeLower.includes('jpeg') || fileTypeLower.includes('jpg')) fileExtension = 'jpg';
      else if (fileTypeLower.includes('png')) fileExtension = 'png';
      else if (fileTypeLower.includes('gif')) fileExtension = 'gif';
      else if (fileTypeLower.includes('bmp')) fileExtension = 'bmp';
      else if (fileTypeLower.includes('webp')) fileExtension = 'webp';
      else if (fileTypeLower.includes('tiff') || fileTypeLower.includes('tif')) fileExtension = 'tiff';
      else if (fileTypeLower.includes('text/plain')) fileExtension = 'txt';
      else if (fileTypeLower.includes('csv')) fileExtension = 'csv';
      else if (fileTypeLower.includes('html')) fileExtension = 'html';
      else if (fileTypeLower.includes('xml')) fileExtension = 'xml';
      else if (fileTypeLower.includes('json')) fileExtension = 'json';
      else fileExtension = fileTypeLower.split('/').pop();
    }
    // If it has a dot, extract extension (e.g., "document.pdf" -> "pdf")
    else if (fileTypeLower.includes('.')) {
      fileExtension = fileTypeLower.split('.').pop();
    }
    // If it's already just an extension (e.g., "pdf", "jpg")
    else {
      fileExtension = fileTypeLower;
    }
  }
  
  console.log('Detected file extension:', fileExtension);
  
  if (fileExtension === 'pdf') {
    // PDF files - use PDF.js or browser's built-in PDF viewer
    const iframe = document.createElement('iframe');
    iframe.src = fileUrl;
    iframe.className = 'pdf-viewer';
    content.appendChild(iframe);
  } else if (['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tiff', 'tif'].includes(fileExtension)) {
    // Image files
    const img = document.createElement('img');
    img.src = fileUrl;
    img.alt = fileName;
    img.onerror = function() {
      content.innerHTML = '<p style="color: #666; text-align: center;">Unable to display image. <a href="' + fileUrl + '" target="_blank">Download instead</a></p>';
    };
    content.appendChild(img);
  } else if (['txt', 'csv', 'html', 'htm', 'xml', 'json'].includes(fileExtension)) {
    // Text files - fetch and display content
    fetch(fileUrl)
      .then(response => response.text())
      .then(text => {
        const pre = document.createElement('pre');
        pre.style.cssText = 'padding: 20px; overflow: auto; max-height: 100%; font-family: monospace; font-size: 14px; line-height: 1.4;';
        pre.textContent = text;
        content.appendChild(pre);
      })
      .catch(error => {
        content.innerHTML = '<p style="color: #666; text-align: center;">Unable to display file. <a href="' + fileUrl + '" target="_blank">Download instead</a></p>';
      });
  } else {
    // Other file types - show download option
    content.innerHTML = `
      <div style="text-align: center; padding: 40px;">
        <p style="color: #666; margin-bottom: 20px;">This file type cannot be previewed.</p>
        <a href="${fileUrl}" target="_blank" class="file-action download" style="display: inline-block;">
          📥 Download File
        </a>
      </div>
    `;
  }
  
  // Prevent body scroll when modal is open
  document.body.style.overflow = 'hidden';
}

function closeFileViewer() {
  const modal = document.getElementById('file-viewer-modal');
  modal.style.display = 'none';
  document.body.style.overflow = 'auto';
}

// Close modal when clicking outside
document.addEventListener('DOMContentLoaded', function() {
  const modal = document.getElementById('file-viewer-modal');
  modal.addEventListener('click', function(e) {
    if (e.target === modal) {
      closeFileViewer();
    }
  });
  
  // Close modal with Escape key
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && modal.style.display === 'flex') {
      closeFileViewer();
    }
  });
});

// Initialize with first stage active
document.addEventListener('DOMContentLoaded', function() {
  const firstStage = document.querySelector('.timeline-item');
  if (firstStage) {
    firstStage.classList.add('active');
    const stageKey = firstStage.getAttribute('onclick').match(/'([^']+)'/)[1];
    selectStage(stageKey);
  }
});

// File Sharing Functions
let currentStageKey = null;
let currentStageFiles = [];
let stagesData = [];

// Initialize stages data when page loads
document.addEventListener('DOMContentLoaded', function() {
  try {
    const jsonString = '{{ stages_json|safe }}';
    console.log('Raw JSON string:', jsonString);
    
    if (!jsonString || jsonString.trim() === '') {
      console.warn('Stages JSON is empty');
      stagesData = [];
      return;
    }
    
    stagesData = JSON.parse(jsonString);
    console.log('Stages data loaded:', stagesData);
  } catch (error) {
    console.error('Error parsing stages data:', error);
    console.error('Raw JSON string was:', '{{ stages_json|safe }}');
    stagesData = [];
  }
});



function openShareFilesModal(stageKey, stageName) {
  currentStageKey = stageKey;
  
  console.log('Opening share modal for stage:', stageKey, stageName);
  
  // Find the stage container in the DOM
  const stageContainer = document.querySelector(`[data-stage-key="${stageKey}"]`);
  if (!stageContainer) {
    console.error('Stage container not found for key:', stageKey);
    alert('Unable to find stage data. Please refresh the page and try again.');
    return;
  }
  
  // Extract file data from the DOM
  const fileElements = stageContainer.querySelectorAll('.file-item');
  const files = [];
  
  fileElements.forEach((fileElement, index) => {
    const fileName = fileElement.querySelector('.file-name')?.textContent || 'Unknown file';
    const fileMeta = fileElement.querySelector('.file-meta')?.textContent || '';
    const downloadLink = fileElement.querySelector('a[href]')?.href || '';
    const viewButton = fileElement.querySelector('button[onclick*="viewFile"]');
    
    // Extract file ID and S3 key from data attributes
    const fileId = fileElement.getAttribute('data-file-id');
    const s3Key = fileElement.getAttribute('data-file-s3-key') || '';
    
    // Parse date from file meta
    const dateMatch = fileMeta.match(/(\w+ \d+, \d{4})/);
    const fileDate = dateMatch ? dateMatch[1] : '';
    
    // Parse description from file meta
    const descMatch = fileMeta.match(/ - (.+)$/);
    const fileDescription = descMatch ? descMatch[1] : '';
    
    files.push({
      id: fileId ? parseInt(fileId) : index,
      name: fileName,
      date: fileDate,
      description: fileDescription,
      s3_key: s3Key,
      download_url: s3Key,
      file_type: 'unknown',
      is_viewable: !!viewButton
    });
  });
  
  console.log('Extracted files from DOM:', files);
  
  if (files.length === 0) {
    alert('No files found for this stage.');
    return;
  }
  
  currentStageFiles = files;
  populateShareFilesList(files);
  document.getElementById('share-stage-name').textContent = stageName;
  
  // Clear the email field to ensure it's empty each time
  document.getElementById('recipient-email').value = '';
  
  document.getElementById('share-files-modal').style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function populateShareFilesList(files) {
  console.log('Populating share files list with:', files);  // Debug logging
  const filesList = document.getElementById('share-files-list');
  filesList.innerHTML = '';
  
  if (!files || files.length === 0) {
    filesList.innerHTML = '<p style="color: #666; text-align: center; padding: 20px;">No files found for this stage.</p>';
    return;
  }
  
           files.forEach((file, index) => {
           console.log('Processing file:', file);  // Debug logging
           const fileItem = document.createElement('div');
           fileItem.style.cssText = 'display: flex; align-items: flex-start; padding: 12px; border: 1px solid #ddd; border-radius: 4px; margin-bottom: 8px; background: white; gap: 12px;';
           
           // Create label wrapper for better accessibility and clickability
           const label = document.createElement('label');
           label.htmlFor = `file-checkbox-${index}`;
           label.style.cssText = 'display: flex; align-items: center; cursor: pointer; margin-right: 12px; min-width: 20px; min-height: 20px;';
           
           const checkbox = document.createElement('input');
           checkbox.type = 'checkbox';
           checkbox.id = `file-checkbox-${index}`;
           checkbox.checked = true; // Pre-select all files by default
           checkbox.style.cssText = 'width: 20px; height: 20px; cursor: pointer; margin: 0; padding: 0; appearance: auto; -webkit-appearance: auto; -moz-appearance: auto; display: inline-block !important; opacity: 1 !important; visibility: visible !important; background: white !important; border: 2px solid #2196F3 !important; border-radius: 3px !important; position: relative !important; z-index: 1 !important;';
           
           // Add click event listener to ensure it's interactive
           checkbox.addEventListener('click', function(e) {
               console.log('Checkbox clicked:', this.checked);
               e.stopPropagation(); // Prevent event bubbling
           });
           
           // Add debugging to ensure checkbox is created
           console.log('Created checkbox:', checkbox);
           console.log('Checkbox visible:', checkbox.offsetWidth > 0 && checkbox.offsetHeight > 0);
           console.log('Checkbox style:', checkbox.style.cssText);
           
           const fileInfo = document.createElement('div');
           fileInfo.style.flex = '1';
           fileInfo.innerHTML = `
             <div style="font-weight: 500; margin-bottom: 2px;">${file.name || 'Unknown file'}</div>
             <div style="font-size: 12px; color: #666;">${file.date ? new Date(file.date).toLocaleDateString() : 'No date'} - ${file.description || 'No description'}</div>
             <div style="font-size: 11px; color: #999;">S3 Key: ${file.s3_key || 'No S3 key'}</div>
           `;
           
           // Append checkbox to label, then label to fileItem
           label.appendChild(checkbox);
           fileItem.appendChild(label);
           fileItem.appendChild(fileInfo);
           filesList.appendChild(fileItem);
           
           // Debug the final structure
           console.log('File item created:', fileItem);
           console.log('Checkbox in DOM:', fileItem.querySelector('input[type="checkbox"]'));
           console.log('Total checkboxes in list:', filesList.querySelectorAll('input[type="checkbox"]').length);
         });
  
  // Final debug check
  console.log('Final files list HTML:', filesList.innerHTML);
  console.log('All checkboxes found:', filesList.querySelectorAll('input[type="checkbox"]'));
}

function closeShareFilesModal() {
  document.getElementById('share-files-modal').style.display = 'none';
  document.body.style.overflow = 'auto';
  currentStageKey = null;
  currentStageFiles = [];
}

function openScheduleModal(stageKey, actionKey, actionDisplayName) {
  console.log('Opening schedule modal:', { stageKey, actionKey, actionDisplayName });
  
  // Set modal content
  document.getElementById('schedule-action-name').textContent = actionDisplayName;
  document.getElementById('schedule-stage-key').value = stageKey;
  document.getElementById('schedule-action-key').value = actionKey;
  
  // Set default date to today
  const today = new Date().toISOString().split('T')[0];
  document.getElementById('consult-date').value = today;
  
  // Set default time to 9 AM
  document.getElementById('consult-time').value = '09:00';
  
  // Clear previous form data
  document.getElementById('doctor-name').value = '';
  document.getElementById('consult-notes').value = '';
  
  // Customize modal based on action type
  const modalTitle = document.getElementById('schedule-modal-title');
  const dateLabel = document.querySelector('label[for="consult-date"]');
  const timeLabel = document.querySelector('label[for="consult-time"]');
  const doctorLabel = document.querySelector('label[for="doctor-name"]');
  
  if (actionKey.includes('sleep_study') || actionKey.includes('sleep_test_review')) {
    modalTitle.textContent = 'Schedule Sleep Study';
    dateLabel.textContent = 'Study Date:';
    timeLabel.textContent = 'Study Time:';
    doctorLabel.textContent = 'Sleep Specialist:';
  } else if (actionKey.includes('dental_consult')) {
    modalTitle.textContent = 'Schedule Dental Consultation';
    dateLabel.textContent = 'Consultation Date:';
    timeLabel.textContent = 'Consultation Time:';
    doctorLabel.textContent = 'Dental Sleep Doctor:';
  } else if (actionKey.includes('cbct_scan')) {
    modalTitle.textContent = 'Schedule CBCT Scan';
    dateLabel.textContent = 'Scan Date:';
    timeLabel.textContent = 'Scan Time:';
    doctorLabel.textContent = 'Radiologist:';
  } else if (actionKey.includes('intraoral_scan')) {
    modalTitle.textContent = 'Schedule Intraoral Scan';
    dateLabel.textContent = 'Scan Date:';
    timeLabel.textContent = 'Scan Time:';
    doctorLabel.textContent = 'Technician:';
  } else if (actionKey.includes('hipaa_signing')) {
    modalTitle.textContent = 'Schedule HIPAA Consent Signing';
    dateLabel.textContent = 'Signing Date:';
    timeLabel.textContent = 'Signing Time:';
    doctorLabel.textContent = 'Administrator:';
  } else if (actionKey.includes('appliance_delivery')) {
    modalTitle.textContent = 'Schedule Appliance Delivery';
    dateLabel.textContent = 'Delivery Date:';
    timeLabel.textContent = 'Delivery Time:';
    doctorLabel.textContent = 'Delivery Provider:';
  } else if (actionKey.includes('followup_sleep_test')) {
    modalTitle.textContent = 'Schedule Follow-up Sleep Test';
    dateLabel.textContent = 'Test Date:';
    timeLabel.textContent = 'Test Time:';
    doctorLabel.textContent = 'Sleep Specialist:';
  } else {
    modalTitle.textContent = 'Schedule Consultation';
    dateLabel.textContent = 'Consultation Date:';
    timeLabel.textContent = 'Consultation Time:';
    doctorLabel.textContent = 'Doctor/Provider Name:';
  }
  
  // Show modal
  document.getElementById('schedule-modal').style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function closeScheduleModal() {
  document.getElementById('schedule-modal').style.display = 'none';
  document.body.style.overflow = 'auto';
}

function openUpdateModal(stageKey, actionKey, actionDisplayName, existingId) {
  console.log('Opening update modal:', { stageKey, actionKey, actionDisplayName, existingId });
  
  // Set modal content
  document.getElementById('update-action-name').textContent = actionDisplayName;
  document.getElementById('update-stage-key').value = stageKey;
  document.getElementById('update-action-key').value = actionKey;
  document.getElementById('update-existing-id').value = existingId;
  
  // Set default date to today
  const today = new Date().toISOString().split('T')[0];
  document.getElementById('update-consult-date').value = today;
  
  // Set default time to 9 AM
  document.getElementById('update-consult-time').value = '09:00';
  
  // Clear previous form data
  document.getElementById('update-doctor-name').value = '';
  document.getElementById('update-consult-notes').value = '';
  
  // Customize modal based on action type
  const modalTitle = document.getElementById('update-modal-title');
  const dateLabel = document.querySelector('label[for="update-consult-date"]');
  const timeLabel = document.querySelector('label[for="update-consult-time"]');
  const doctorLabel = document.querySelector('label[for="update-doctor-name"]');
  
  if (actionKey.includes('sleep_study') || actionKey.includes('sleep_test_review')) {
    modalTitle.textContent = 'Update Sleep Study';
    dateLabel.textContent = 'Study Date:';
    timeLabel.textContent = 'Study Time:';
    doctorLabel.textContent = 'Sleep Specialist:';
  } else if (actionKey.includes('dental_consult')) {
    modalTitle.textContent = 'Update Dental Consultation';
    dateLabel.textContent = 'Consultation Date:';
    timeLabel.textContent = 'Consultation Time:';
    doctorLabel.textContent = 'Dental Sleep Doctor:';
  } else if (actionKey.includes('cbct_scan')) {
    modalTitle.textContent = 'Update CBCT Scan';
    dateLabel.textContent = 'Scan Date:';
    timeLabel.textContent = 'Scan Time:';
    doctorLabel.textContent = 'Radiologist:';
  } else if (actionKey.includes('intraoral_scan')) {
    modalTitle.textContent = 'Update Intraoral Scan';
    dateLabel.textContent = 'Scan Date:';
    timeLabel.textContent = 'Scan Time:';
    doctorLabel.textContent = 'Technician:';
  } else if (actionKey.includes('hipaa_signing')) {
    modalTitle.textContent = 'Update HIPAA Consent Signing';
    dateLabel.textContent = 'Signing Date:';
    timeLabel.textContent = 'Signing Time:';
    doctorLabel.textContent = 'Administrator:';
  } else if (actionKey.includes('appliance_delivery')) {
    modalTitle.textContent = 'Update Appliance Delivery';
    dateLabel.textContent = 'Delivery Date:';
    timeLabel.textContent = 'Delivery Time:';
    doctorLabel.textContent = 'Delivery Provider:';
  } else if (actionKey.includes('followup_sleep_test')) {
    modalTitle.textContent = 'Update Follow-up Sleep Test';
    dateLabel.textContent = 'Test Date:';
    timeLabel.textContent = 'Test Time:';
    doctorLabel.textContent = 'Sleep Specialist:';
  } else {
    modalTitle.textContent = 'Update Consultation';
    dateLabel.textContent = 'Consultation Date:';
    timeLabel.textContent = 'Consultation Time:';
    doctorLabel.textContent = 'Doctor/Provider Name:';
  }
  
  // Show modal
  document.getElementById('update-modal').style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function closeUpdateModal() {
  document.getElementById('update-modal').style.display = 'none';
  document.body.style.overflow = 'auto';
}

function executeStageAction(stageKey, actionType, actionKey) {
  console.log('Executing stage action:', { stageKey, actionType, actionKey });
  
  // Get patient ID from URL
  const urlParts = window.location.pathname.split('/');
  const patientId = urlParts[urlParts.length - 1];
  
  // Prepare request data
  const requestData = {
    patient_id: parseInt(patientId),
    action_type: actionKey
  };
  
  // Add additional data based on action type
  if (actionType === 'schedule') {
    requestData.details = {
      scheduled_date: new Date().toISOString().split('T')[0], // Today's date
      notes: `Scheduled via patient journey interface`
    };
  } else if (actionType === 'confirm') {
    requestData.details = {
      notes: `Confirmed via patient journey interface`
    };
  }
  
  // Show loading state
  const button = event.target;
  const originalText = button.textContent;
  button.textContent = '⏳ Processing...';
  button.disabled = true;
  
  // Make API call
  fetch(`/api/stage/${stageKey}/${actionType}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(requestData)
  })
  .then(response => response.json())
           .then(data => {
           if (data.success) {
             // Show success message
             alert(data.message);
             
             // Refresh the current page to show updated information
             window.location.reload();
           } else {
      // Show error message
      alert('Error: ' + (data.error || 'Unknown error occurred'));
      
      // Reset button
      button.textContent = originalText;
      button.disabled = false;
    }
  })
  .catch(error => {
    console.error('Error executing stage action:', error);
    alert('Error executing action. Please try again.');
    
    // Reset button
    button.textContent = originalText;
    button.disabled = false;
  });
}

// Handle schedule form submission
document.getElementById('schedule-form').addEventListener('submit', function(e) {
    e.preventDefault();
    
    const scheduleButton = document.getElementById('schedule-btn');
    const originalText = scheduleButton.textContent;
    
    // Disable button and show loading state
    scheduleButton.disabled = true;
    scheduleButton.style.background = '#ccc';
    scheduleButton.style.cursor = 'not-allowed';
    scheduleButton.textContent = 'Scheduling...';
    
    // Get form data
    const stageKey = document.getElementById('schedule-stage-key').value;
    const actionKey = document.getElementById('schedule-action-key').value;
    const consultDate = document.getElementById('consult-date').value;
    const consultTime = document.getElementById('consult-time').value;
    const doctorName = document.getElementById('doctor-name').value;
    const consultNotes = document.getElementById('consult-notes').value;
    
    // Validate required fields
    if (!consultDate || !consultTime || !doctorName) {
        alert('Please fill in all required fields.');
        // Reset button state
        scheduleButton.disabled = false;
        scheduleButton.style.background = '#007bff';
        scheduleButton.style.cursor = 'pointer';
        scheduleButton.textContent = originalText;
        return;
    }
    
    // Get patient ID from URL
    const urlParts = window.location.pathname.split('/');
    const patientId = urlParts[urlParts.length - 1];
    
    // Combine date and time
    const scheduledDateTime = `${consultDate}T${consultTime}`;
    
    // Prepare request data
    const requestData = {
        patient_id: parseInt(patientId),
        action_type: actionKey,
        details: {
            scheduled_date: scheduledDateTime,
            doctor_name: doctorName,
            notes: consultNotes || `Scheduled via patient journey interface`
        }
    };
    
    // Make API call
    fetch(`/api/stage/${stageKey}/schedule`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestData)
    })
    .then(response => response.json())
               .then(data => {
               if (data.success) {
                   // Show success message
                   alert(data.message);
                   
                   // Close modal
                   closeScheduleModal();
                   
                   // Refresh the current page to show updated information
                   window.location.reload();
               } else {
            // Show error message
            alert('Error: ' + (data.error || 'Unknown error occurred'));
            
            // Reset button
            scheduleButton.disabled = false;
            scheduleButton.style.background = '#007bff';
            scheduleButton.style.cursor = 'pointer';
            scheduleButton.textContent = originalText;
        }
    })
    .catch(error => {
        console.error('Error scheduling consultation:', error);
        alert('Error scheduling consultation. Please try again.');
        
        // Reset button
        scheduleButton.disabled = false;
        scheduleButton.style.background = '#007bff';
        scheduleButton.style.cursor = 'pointer';
        scheduleButton.textContent = originalText;
    });
});

// Handle share form submission
document.getElementById('share-files-form').addEventListener('submit', function(e) {
            e.preventDefault();
            
            const sendButton = document.getElementById('send-files-btn');
            const originalText = sendButton.textContent;
            
            // Disable button and show loading state
            sendButton.disabled = true;
            sendButton.style.background = '#ccc';
            sendButton.style.cursor = 'not-allowed';
            sendButton.textContent = 'Sending...';
            
            const recipientEmails = document.getElementById('recipient-email').value.trim();
            const message = document.getElementById('email-message').value.trim();
            
            if (!recipientEmails) {
                alert('Please enter at least one recipient email address.');
                // Reset button state
                sendButton.disabled = false;
                sendButton.style.background = 'var(--primary-color)';
                sendButton.style.cursor = 'pointer';
                sendButton.textContent = originalText;
                return;
            }
            
            // Parse multiple email addresses
            const emailList = recipientEmails.split(',').map(email => email.trim()).filter(email => email.length > 0);
            
            if (emailList.length === 0) {
                alert('Please enter at least one valid email address.');
                // Reset button state
                sendButton.disabled = false;
                sendButton.style.background = 'var(--primary-color)';
                sendButton.style.cursor = 'pointer';
                sendButton.textContent = originalText;
                return;
            }
            
            // Get selected files by checking which checkboxes are checked
            const selectedItems = [];
            const checkboxes = document.querySelectorAll('#share-files-list input[type="checkbox"]:checked');
            
            checkboxes.forEach((checkbox, index) => {
                const fileItem = checkbox.closest('div');
                const fileInfo = fileItem.querySelector('div:last-child');
                const fileName = fileInfo.querySelector('div:first-child').textContent;
                const s3KeyLine = fileInfo.querySelector('div:last-child').textContent;
                const s3Key = s3KeyLine.replace('S3 Key: ', '');
                
                // Find the corresponding file in currentStageFiles
                const file = currentStageFiles.find(f => f.name === fileName && f.s3_key === s3Key);
                
                if (file) {
                    selectedItems.push({
                        isFolder: false,
                        fileId: file.id,
                        folderName: null,
                        category: file.file_type || 'unknown',
                        patientId: {{ patient.id }},
                        s3_key: file.s3_key,
                        name: file.name
                    });
                }
            });
            
            console.log('Selected items for sharing:', selectedItems);
            
            if (selectedItems.length === 0) {
                alert('No files selected to share. Please select at least one file.');
                // Reset button state
                sendButton.disabled = false;
                sendButton.style.background = 'var(--primary-color)';
                sendButton.style.cursor = 'pointer';
                sendButton.textContent = originalText;
                return;
            }
            
            // Send separate requests for each recipient to maintain backward compatibility
            let successCount = 0;
            let errorCount = 0;
            const totalRecipients = emailList.length;
            
            // Update button text to show progress
            sendButton.textContent = `Sending... (0/${totalRecipients})`;
            
            // Send to each recipient separately
            const sendPromises = emailList.map((email, index) => {
                return fetch('/generate_presigned_links', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        items: selectedItems,
                        recipient_email: email.trim(),
                        message: message
                    })
                })
                .then(response => response.json())
                .then(data => {
                    console.log(`Share files response for ${email}:`, data);
                    
                    if (data.success) {
                        successCount++;
                        console.log(`✅ Successfully sent to ${email}`);
                    } else {
                        errorCount++;
                        console.error(`❌ Failed to send to ${email}: ${data.message || 'Unknown error'}`);
                    }
                    
                    // Update progress
                    sendButton.textContent = `Sending... (${successCount + errorCount}/${totalRecipients})`;
                    
                    return { email, success: data.success, message: data.message };
                })
                .catch(error => {
                    errorCount++;
                    console.error(`❌ Error sending to ${email}:`, error);
                    sendButton.textContent = `Sending... (${successCount + errorCount}/${totalRecipients})`;
                    return { email, success: false, message: error.message };
                });
            });
            
            // Wait for all requests to complete
            Promise.all(sendPromises)
                .then(results => {
                    const successfulEmails = results.filter(r => r.success).map(r => r.email);
                    const failedEmails = results.filter(r => !r.success).map(r => r.email);
                    
                    if (successfulEmails.length > 0) {
                        let message = `Files shared successfully! Emails sent to: ${successfulEmails.join(', ')}`;
                        if (failedEmails.length > 0) {
                            message += `\n\nFailed to send to: ${failedEmails.join(', ')}`;
                        }
                        alert(message);
                        closeShareFilesModal();
                    } else {
                        alert('Failed to send files to any recipients. Please check the email addresses and try again.');
                    }
                })
                .catch(error => {
                    console.error('Error in batch sending:', error);
                    alert('Error sharing files: ' + error.message);
                })
                .finally(() => {
                    // Reset button state regardless of success or failure
                    sendButton.disabled = false;
                    sendButton.style.background = 'var(--primary-color)';
                    sendButton.style.cursor = 'pointer';
                    sendButton.textContent = originalText;
                });
        });

// Close share modal when clicking outside
document.addEventListener('DOMContentLoaded', function() {
  const shareModal = document.getElementById('share-files-modal');
  shareModal.addEventListener('click', function(e) {
    if (e.target === shareModal) {
      closeShareFilesModal();
    }
  });
  
  // Close share modal with Escape key
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && shareModal.style.display === 'flex') {
      closeShareFilesModal();
    }
  });
        });
        

</script>


{% endblock %} 
