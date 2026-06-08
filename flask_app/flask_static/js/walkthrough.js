/**
 * Walkthrough Helper for Intro.js
 * 
 * This helper provides easy-to-use functions for creating interactive walkthroughs
 * in your application using Intro.js.
 */

// Store walkthrough configurations
const WalkthroughManager = {
    walkthroughs: {},
    
    /**
     * Register a new walkthrough
     * @param {string} name - Unique name for the walkthrough
     * @param {Array} steps - Array of step objects with intro.js configuration
     */
    register: function(name, steps) {
        this.walkthroughs[name] = steps;
    },
    
    /**
     * Start a walkthrough by name
     * @param {string} name - Name of the registered walkthrough
     * @param {Object} options - Optional Intro.js options (see intro.js docs)
     */
    start: function(name, options = {}) {
        if (!this.walkthroughs[name]) {
            console.error(`Walkthrough "${name}" not found. Registered walkthroughs:`, Object.keys(this.walkthroughs));
            return;
        }
        
        const defaultOptions = {
            showProgress: true,
            showBullets: true,
            exitOnOverlayClick: true,
            exitOnEsc: true,
            nextLabel: 'Next &rarr;',
            prevLabel: '&larr; Previous',
            skipLabel: 'Skip Tour',
            doneLabel: 'Got it!',
            tooltipClass: 'customTooltip',
            highlightClass: 'customHighlight',
            ...options
        };
        
        introJs().setOptions({
            steps: this.walkthroughs[name],
            ...defaultOptions
        }).start();
    },
    
    /**
     * Create a quick walkthrough from data attributes on elements
     * Elements should have data-intro and data-step attributes
     * @param {string} name - Name to register the walkthrough as
     */
    createFromElements: function(name) {
        const elements = document.querySelectorAll('[data-intro]');
        const steps = [];
        
        elements.forEach((element, index) => {
            steps.push({
                element: element,
                intro: element.getAttribute('data-intro'),
                title: element.getAttribute('data-title') || '',
                position: element.getAttribute('data-position') || 'bottom',
                tooltipClass: element.getAttribute('data-tooltip-class') || 'customTooltip'
            });
        });
        
        // Sort by data-step if available
        steps.sort((a, b) => {
            const stepA = parseInt(a.element.getAttribute('data-step') || '0');
            const stepB = parseInt(b.element.getAttribute('data-step') || '0');
            return stepA - stepB;
        });
        
        this.register(name, steps);
        return steps;
    },
    
    /**
     * Check if a walkthrough should be shown (first time visit)
     * @param {string} name - Walkthrough name
     * @returns {boolean}
     */
    shouldShow: function(name) {
        const key = `walkthrough_${name}_shown`;
        return !localStorage.getItem(key);
    },
    
    /**
     * Mark a walkthrough as shown
     * @param {string} name - Walkthrough name
     */
    markAsShown: function(name) {
        const key = `walkthrough_${name}_shown`;
        localStorage.setItem(key, 'true');
    },
    
    /**
     * Reset walkthrough (useful for testing or allowing users to see it again)
     * @param {string} name - Walkthrough name (or 'all' to reset all)
     */
    reset: function(name = 'all') {
        if (name === 'all') {
            Object.keys(this.walkthroughs).forEach(key => {
                localStorage.removeItem(`walkthrough_${key}_shown`);
            });
        } else {
            localStorage.removeItem(`walkthrough_${name}_shown`);
        }
    }
};

// Auto-start walkthroughs marked with data-auto-start attribute
document.addEventListener('DOMContentLoaded', function() {
    const autoStartElements = document.querySelectorAll('[data-walkthrough-auto-start]');
    autoStartElements.forEach(element => {
        const walkthroughName = element.getAttribute('data-walkthrough-auto-start');
        if (WalkthroughManager.shouldShow(walkthroughName)) {
            // Wait a bit for page to be fully rendered
            setTimeout(() => {
                WalkthroughManager.start(walkthroughName);
                WalkthroughManager.markAsShown(walkthroughName);
            }, 500);
        }
    });
});

// Global function for easy access
window.startWalkthrough = function(name, options) {
    WalkthroughManager.start(name, options);
};

window.WalkthroughManager = WalkthroughManager;
