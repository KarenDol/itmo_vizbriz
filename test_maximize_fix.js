// Test script to verify Dr. Briz Chat maximize functionality
console.log('🔍 Testing Dr. Briz Chat maximize functionality...');

// Check if maximize button exists
const maximizeBtn = document.getElementById('maximize-btn');
if (maximizeBtn) {
    console.log('✅ Maximize button found:', maximizeBtn);
    console.log('Button text:', maximizeBtn.textContent);
    console.log('Button title:', maximizeBtn.title);
    
    // Test if click handler exists
    const clickHandlers = maximizeBtn.onclick;
    console.log('Click handlers:', clickHandlers);
    
    // Add click handler manually if it doesn't exist
    if (!maximizeBtn.onclick) {
        console.log('⚠️ No click handler found, adding one...');
        
        // Get chat window
        const chatWindow = document.getElementById('dr-briz-chat');
        if (chatWindow) {
            let isMaximized = false;
            let originalPosition = { x: 0, y: 0 };
            let originalSize = { width: 0, height: 0 };
            
            maximizeBtn.onclick = function() {
                if (isMaximized) {
                    // Restore to original size
                    chatWindow.style.width = originalSize.width + 'px';
                    chatWindow.style.height = originalSize.height + 'px';
                    chatWindow.style.left = originalPosition.x + 'px';
                    chatWindow.style.top = originalPosition.y + 'px';
                    maximizeBtn.textContent = '□';
                    isMaximized = false;
                    console.log('🔄 Restored chat window');
                } else {
                    // Store original position and size
                    originalPosition.x = parseInt(chatWindow.style.left) || 0;
                    originalPosition.y = parseInt(chatWindow.style.top) || 0;
                    originalSize.width = chatWindow.offsetWidth;
                    originalSize.height = chatWindow.offsetHeight;
                    
                    // Maximize to full screen
                    chatWindow.style.width = '90vw';
                    chatWindow.style.height = '80vh';
                    chatWindow.style.left = '5vw';
                    chatWindow.style.top = '10vh';
                    maximizeBtn.textContent = '❐';
                    isMaximized = true;
                    console.log('📈 Maximized chat window');
                }
            };
            
            console.log('✅ Maximize functionality added!');
        } else {
            console.log('❌ Chat window not found');
        }
    } else {
        console.log('✅ Click handler already exists');
    }
} else {
    console.log('❌ Maximize button not found');
}

// Check if chat window exists
const chatWindow = document.getElementById('dr-briz-chat');
if (chatWindow) {
    console.log('✅ Chat window found:', chatWindow);
    console.log('Current size:', chatWindow.offsetWidth, 'x', chatWindow.offsetHeight);
    console.log('Current position:', chatWindow.style.left, chatWindow.style.top);
} else {
    console.log('❌ Chat window not found');
}

console.log('🎯 Test completed. Try clicking the maximize button (□) now!'); 