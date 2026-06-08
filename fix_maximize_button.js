// Fix for Dr. Briz Chat maximize button
// Run this in your browser console on the patient journey page

console.log('🔧 Fixing Dr. Briz Chat maximize button...');

// Get the elements
const maximizeBtn = document.getElementById('maximize-btn');
const chatWindow = document.getElementById('dr-briz-chat');

if (maximizeBtn && chatWindow) {
    console.log('✅ Found maximize button and chat window');
    
    // Add maximize functionality
    let isMaximized = false;
    let originalPosition = { x: 0, y: 0 };
    let originalSize = { width: 0, height: 0 };
    
    maximizeBtn.onclick = function() {
        console.log('🎯 Maximize button clicked!');
        
        if (isMaximized) {
            // Restore to original size
            chatWindow.style.width = originalSize.width + 'px';
            chatWindow.style.height = originalSize.height + 'px';
            chatWindow.style.left = originalPosition.x + 'px';
            chatWindow.style.top = originalPosition.y + 'px';
            maximizeBtn.textContent = '□';
            isMaximized = false;
            console.log('🔄 Restored chat window to original size');
        } else {
            // Store original position and size
            originalPosition.x = parseInt(chatWindow.style.left) || 0;
            originalPosition.y = parseInt(chatWindow.style.top) || 0;
            originalSize.width = chatWindow.offsetWidth;
            originalSize.height = chatWindow.offsetHeight;
            
            console.log('📏 Original size:', originalSize.width, 'x', originalSize.height);
            console.log('📍 Original position:', originalPosition.x, originalPosition.y);
            
            // Maximize to full screen
            chatWindow.style.width = '90vw';
            chatWindow.style.height = '80vh';
            chatWindow.style.left = '5vw';
            chatWindow.style.top = '10vh';
            maximizeBtn.textContent = '❐';
            isMaximized = true;
            console.log('📈 Maximized chat window to 90vw x 80vh');
        }
    };
    
    console.log('✅ Maximize functionality added successfully!');
    console.log('🎯 Try clicking the maximize button (□) now!');
} else {
    console.log('❌ Could not find maximize button or chat window');
    console.log('Maximize button:', !!maximizeBtn);
    console.log('Chat window:', !!chatWindow);
} 