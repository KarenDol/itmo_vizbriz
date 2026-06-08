/**
 * Engagement Tracking System
 * Tracks user interactions, page views, and CTA clicks for quiz analytics
 */

class EngagementTracker {
    constructor() {
        this.sessionId = this.generateSessionId();
        this.baseUrl = '/api/tracking';
        this.debugMode = true; // Set to true for console logging
    }

    /**
     * Generate a unique session ID for the browser session
     */
    generateSessionId() {
        // Check if session ID already exists
        let sessionId = sessionStorage.getItem('quiz_session_id');
        if (!sessionId) {
            sessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
            sessionStorage.setItem('quiz_session_id', sessionId);
        }
        return sessionId;
    }

    /**
     * Get current patient email from various sources
     */
    getPatientEmail() {
        // Try to get email from form fields
        const emailField = document.getElementById('patient_email') || 
                          document.querySelector('input[name="patient_email"]') ||
                          document.querySelector('input[type="email"]');
        
        if (emailField && emailField.value) {
            return emailField.value;
        }

        // Try to get from session storage
        return sessionStorage.getItem('patient_email') || null;
    }

    /**
     * Extract clinic ID from URL parameters or page data
     */
    getClinicId() {
        const urlParams = new URLSearchParams(window.location.search);
        return urlParams.get('clinic_id') || 
               window.clinicId || 
               window.defaultClinicId ||
               document.querySelector('[data-clinic-id]')?.getAttribute('data-clinic-id') || 
               null; // Fallback to null if no clinic found
    }

    /**
     * Extract UTM parameters for campaign tracking
     */
    getUTMParams() {
        const urlParams = new URLSearchParams(window.location.search);
        return {
            utm_source: urlParams.get('utm_source'),
            utm_medium: urlParams.get('utm_medium'),
            utm_campaign: urlParams.get('utm_campaign')
        };
    }

    /**
     * Get basic client information
     */
    getClientInfo() {
        return {
            user_agent: navigator.userAgent,
            referrer: document.referrer || null,
            page_url: window.location.href
        };
    }

    /**
     * Track page view
     */
    async trackPageView(pageType, additionalData = {}) {
        const data = {
            patient_email: this.getPatientEmail(),
            session_id: this.sessionId,
            page_type: pageType,
            clinic_id: this.getClinicId(),
            ...this.getClientInfo(),
            ...this.getUTMParams(),
            ...additionalData
        };

        await this.sendTrackingData('/track-page-view', data);
        
        if (this.debugMode) {
            console.log('Page view tracked:', data);
        }
    }

    /**
     * Track CTA interaction
     */
    async trackCTAClick(ctaType, ctaText, additionalData = {}) {
        const data = {
            patient_email: this.getPatientEmail(),
            session_id: this.sessionId,
            cta_type: ctaType,
            cta_text: ctaText,
            page_type: this.getCurrentPageType(),
            quiz_type: this.getQuizType(),
            clinic_id: this.getClinicId(),
            ...this.getClientInfo(),
            ...additionalData
        };

        await this.sendTrackingData('/track-cta-click', data);
        
        if (this.debugMode) {
            console.log('CTA click tracked:', data);
        }
    }

    /**
     * Track email delivery
     */
    async trackEmailDelivery(recipientEmail, recipientType, emailType, additionalData = {}) {
        const data = {
            recipient_email: recipientEmail,
            recipient_type: recipientType,
            email_type: emailType,
            patient_email: this.getPatientEmail(),
            clinic_id: this.getClinicId(),
            ...additionalData
        };

        await this.sendTrackingData('/track-email-delivery', data);
        
        if (this.debugMode) {
            console.log('Email delivery tracked:', data);
        }
    }

    /**
     * Determine current page type based on URL and content
     */
    getCurrentPageType() {
        const path = window.location.pathname;
        const url = window.location.href;
        
        if (url.includes('conversion_quiz') || path.includes('quiz')) {
            return 'stage_a_step_1';  // Default to step 1, will be overridden by specific tracking calls
        } else if (url.includes('advanced_quiz') || document.getElementById('advancedQuizForm')) {
            return 'stage_b_step_1';  // Default to step 1, will be overridden by specific tracking calls
        } else if (url.includes('results') || document.querySelector('.quiz-results')) {
            return 'results_page';
        }
        
        return 'unknown';
    }

    /**
     * Determine quiz type
     */
    getQuizType() {
        if (document.getElementById('advancedQuizForm') || window.location.href.includes('advanced')) {
            return 'advanced_quiz';
        } else if (document.getElementById('quizForm') || window.location.href.includes('quiz')) {
            return 'basic_quiz';
        }
        return 'unknown';
    }

    /**
     * Send tracking data to backend
     */
    async sendTrackingData(endpoint, data) {
        try {
            const response = await fetch(this.baseUrl + endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(data)
            });

            if (!response.ok) {
                console.warn('Tracking request failed:', response.status);
            }
        } catch (error) {
            console.warn('Tracking error:', error);
        }
    }

    /**
     * Store patient email for future tracking
     */
    setPatientEmail(email) {
        if (email) {
            sessionStorage.setItem('patient_email', email);
        }
    }

    /**
     * Initialize automatic tracking
     */
    init() {
        // Track page view on load
        document.addEventListener('DOMContentLoaded', () => {
            this.trackPageView(this.getCurrentPageType());
        });

        // Track form submissions for quiz completion
        document.addEventListener('submit', (e) => {
            const form = e.target;
            if (form.id === 'quizForm' || form.classList.contains('quiz-form')) {
                this.trackCTAClick('quiz_submission', 'Submit Quiz', {
                    quiz_type: this.getQuizType()
                });
            }
        });

        // Track email field when filled
        document.addEventListener('input', (e) => {
            if (e.target.type === 'email' && e.target.value.includes('@')) {
                this.setPatientEmail(e.target.value);
            }
        });

        // Track page visibility changes (user leaves/returns)
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                this.trackPageView(this.getCurrentPageType() + '_return');
            }
        });
    }

    /**
     * Setup CTA button tracking
     */
    setupCTATracking() {
        // Track "Schedule Sleep Test" buttons
        document.querySelectorAll('[data-cta="schedule-sleep-test"], .schedule-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.trackCTAClick('schedule_sleep_test', btn.textContent.trim());
            });
        });

        // Track buttons containing "Schedule" text
        document.querySelectorAll('button, a').forEach(btn => {
            const text = btn.textContent.toLowerCase();
            if (text.includes('schedule') && text.includes('sleep')) {
                btn.addEventListener('click', (e) => {
                    this.trackCTAClick('schedule_sleep_test', btn.textContent.trim());
                });
            }
        });

        // Track "Complete Advanced Assessment" buttons
        document.querySelectorAll('[data-cta="advanced-assessment"], .advanced-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.trackCTAClick('complete_advanced_assessment', btn.textContent.trim());
            });
        });

        // Track buttons containing "Advanced" or "Assessment" text
        document.querySelectorAll('button, a').forEach(btn => {
            const text = btn.textContent.toLowerCase();
            if ((text.includes('advanced') || text.includes('detailed')) && text.includes('assessment')) {
                btn.addEventListener('click', (e) => {
                    this.trackCTAClick('complete_advanced_assessment', btn.textContent.trim());
                });
            }
        });

        // Track email clicks (clinic email addresses)
        document.querySelectorAll('a[href^="mailto:"]').forEach(link => {
            link.addEventListener('click', (e) => {
                this.trackCTAClick('email_click', link.href);
            });
        });

        // Track phone clicks
        document.querySelectorAll('a[href^="tel:"]').forEach(link => {
            link.addEventListener('click', (e) => {
                this.trackCTAClick('phone_click', link.href);
            });
        });

        // Track any button with tracking attributes
        document.querySelectorAll('[data-track-cta]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const ctaType = btn.getAttribute('data-track-cta');
                const ctaText = btn.getAttribute('data-cta-text') || btn.textContent.trim();
                this.trackCTAClick(ctaType, ctaText);
            });
        });
    }
}

// Initialize global tracker
window.engagementTracker = new EngagementTracker();

// Auto-initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    window.engagementTracker.init();
    window.engagementTracker.setupCTATracking();
});

// Export for manual usage
window.trackPageView = (pageType, data) => window.engagementTracker.trackPageView(pageType, data);
window.trackCTAClick = (ctaType, ctaText, data) => window.engagementTracker.trackCTAClick(ctaType, ctaText, data);
window.trackEmailDelivery = (email, type, emailType, data) => window.engagementTracker.trackEmailDelivery(email, type, emailType, data); 