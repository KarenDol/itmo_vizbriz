// Debug script for Dr. Briz chat functionality
console.log("=== DR. BRIZ CHAT DEBUG ===");

// Test if patientData is available
if (typeof patientData !== 'undefined') {
    console.log("✅ patientData is defined");
    console.log("patientData.id:", patientData.id);
    console.log("patientData.name:", patientData.name);
    console.log("patientData.stages:", patientData.stages);
} else {
    console.log("❌ patientData is NOT defined");
}

// Test the sendMessageToBackend function
function testChatMessage() {
    console.log("Testing chat message...");
    
    if (typeof patientData === 'undefined') {
        console.log("❌ Cannot test - patientData not available");
        return;
    }
    
    const testMessage = "What stage is the patient currently in?";
    console.log("Sending test message:", testMessage);
    
    fetch('/osaagent/api/bedrock_chat', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            patient_id: patientData.id,
            message: testMessage,
            manifest: patientData.stages
        })
    })
    .then(response => {
        console.log("Response status:", response.status);
        return response.json();
    })
    .then(data => {
        console.log("Response data:", data);
        if (data.success) {
            console.log("✅ Chat response:", data.response);
        } else {
            console.log("❌ Chat error:", data.message);
        }
    })
    .catch(error => {
        console.log("❌ Network error:", error);
    });
}

// Run the test
testChatMessage(); 