/**
 * Unified Tracking Utility
 * Provides easy-to-use functions for tracking user interactions
 * Uses the new /api/tracking/log-interaction endpoint
 */

class UnifiedTracker {
    constructor() {
        this.baseUrl = '/api/tracking';
        this.sessionId = this.generateSessionId();
        this.quizId = null;
        this.patientEmail = null;
        this.quizType = null;
        this.clinicId = null;
    }

    /**
     * Generate a unique session ID
     */
    generateSessionId() {
        return 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    }

    /**
     * Set quiz context for all tracking calls
     */
    setQuizContext(quizId, patientEmail = null, quizType = null, clinicId = null) {
        this.quizId = quizId;
        this.patientEmail = patientEmail;
        this.quizType = quizType;
        this.clinicId = clinicId;
        console.log('Quiz context set:', { quizId, patientEmail, quizType, clinicId });
    }

    /**
     * Track a page view
     */
    async trackPageView(pageType, additionalData = {}) {
        const data = {
            interaction_type: 'page_view',
            quiz_id: this.quizId,
            patient_email: this.patientEmail,
            session_id: this.sessionId,
            page_type: pageType,
            quiz_type: this.quizType,
            clinic_id: this.clinicId,
            page_url: window.location.href,
            referrer: document.referrer,
            user_agent: navigator.userAgent,
            ...additionalData
        };

        return await this.logInteraction(data);
    }

    /**
     * Track a CTA click
     */
    async trackCTAClick(ctaType, ctaText, pageType = null, emailSource = false, additionalData = {}) {
        const data = {
            interaction_type: 'cta_click',
            quiz_id: this.quizId,
            patient_email: this.patientEmail,
            session_id: this.sessionId,
            cta_type: ctaType,
            cta_text: ctaText,
            page_type: pageType,
            email_source: emailSource,
            quiz_type: this.quizType,
            clinic_id: this.clinicId,
            ...additionalData
        };

        return await this.logInteraction(data);
    }

    /**
     * Track a form submission
     */
    async trackFormSubmit(formType, additionalData = {}) {
        const data = {
            interaction_type: 'form_submit',
            quiz_id: this.quizId,
            patient_email: this.patientEmail,
            session_id: this.sessionId,
            page_type: formType,
            quiz_type: this.quizType,
            clinic_id: this.clinicId,
            additional_data: {
                form_type: formType,
                submission_time: new Date().toISOString(),
                ...additionalData
            }
        };

        return await this.logInteraction(data);
    }

    /**
     * Generic method to log any interaction
     */
    async logInteraction(data) {
        try {
            const response = await fetch(`${this.baseUrl}/log-interaction`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(data)
            });

            const result = await response.json();

            if (result.status === 'success') {
                console.log('Interaction tracked successfully:', {
                    interaction_id: result.interaction_id,
                    type: data.interaction_type,
                    quiz_id: result.quiz_id
                });
                return result;
            } else {
                console.error('Failed to track interaction:', result.message);
                return result;
            }
        } catch (error) {
            console.error('Error tracking interaction:', error);
            return { status: 'error', message: error.message };
        }
    }

    /**
     * Generate a new quiz ID
     */
    async generateQuizId() {
        try {
            const response = await fetch('/api/generate_quiz_id', {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const result = await response.json();

            if (result.status === 'success') {
                this.quizId = result.quiz_id;
                console.log('Generated quiz ID:', this.quizId);
                return result.quiz_id;
            } else {
                console.error('Failed to generate quiz ID:', result.message);
                return null;
            }
        } catch (error) {
            console.error('Error generating quiz ID:', error);
            return null;
        }
    }

    /**
     * Get current tracking context
     */
    getContext() {
        return {
            sessionId: this.sessionId,
            quizId: this.quizId,
            patientEmail: this.patientEmail,
            quizType: this.quizType,
            clinicId: this.clinicId
        };
    }
}

// Create a global instance
window.unifiedTracker = new UnifiedTracker();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = UnifiedTracker;
} 