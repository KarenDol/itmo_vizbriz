// Test script for chatbot with vector database integration
// Run this in your browser's console while logged into the app

async function testChatbotWithVectorDB() {
    console.log("🧪 Testing Chatbot with Vector Database Integration");
    console.log("=" * 60);
    
    const baseUrl = "http://13.58.61.189:7000";
    const patientId = 95193; // Use your test patient ID
    
    // Test questions that should benefit from vector database knowledge
    const testQuestions = [
        "What are the treatment options for sleep apnea?",
        "How is OSA diagnosed?",
        "What is the Lambert Protocol?",
        "What are the side effects of CPAP therapy?",
        "What is oral appliance therapy?"
    ];
    
    for (let i = 0; i < testQuestions.length; i++) {
        const question = testQuestions[i];
        console.log(`\n🔍 Test ${i + 1}: ${question}`);
        console.log("-".repeat(50));
        
        try {
            const response = await fetch(`${baseUrl}/api/bedrock_chat`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    patient_id: patientId,
                    message: question,
                    workflow_mode: false
                })
            });
            
            if (response.ok) {
                const data = await response.json();
                console.log(`✅ Success: ${data.success}`);
                console.log(`📝 Response: ${data.response}`);
                console.log(`👤 Patient: ${data.patient_name}`);
                
                // Check if response shows signs of vector database usage
                const responseText = data.response.toLowerCase();
                const vectorIndicators = [
                    'medical knowledge', 'knowledge base', 'clinical guidelines',
                    'treatment protocols', 'diagnostic criteria', 'therapeutic options',
                    'sleep medicine', 'osa treatment', 'cpap therapy'
                ];
                
                const hasVectorIndicators = vectorIndicators.some(indicator => 
                    responseText.includes(indicator)
                );
                
                console.log(`🔍 Vector DB indicators: ${hasVectorIndicators ? '✅ Found' : '❌ Not found'}`);
                
            } else {
                console.log(`❌ Error: ${response.status}`);
                const errorText = await response.text();
                console.log(`📝 Error: ${errorText}`);
            }
            
        } catch (error) {
            console.log(`💥 Exception: ${error.message}`);
        }
        
        // Wait 2 seconds between tests
        await new Promise(resolve => setTimeout(resolve, 2000));
    }
    
    console.log("\n" + "=".repeat(60));
    console.log("🎯 CONCLUSION:");
    console.log("If you see detailed medical responses with professional terminology,");
    console.log("the chatbot is likely using the vector database.");
    console.log("If responses are generic or brief, it may not be using the vector database yet.");
    console.log("\n💡 To verify vector database usage:");
    console.log("1. Check the Flask app logs for '✅ Retrieved vector knowledge'");
    console.log("2. Look for '=== QUERYING VECTOR DATABASE INTERNALLY ==='");
    console.log("3. Compare response quality with and without vector database");
}

// Instructions for use
console.log("📋 INSTRUCTIONS:");
console.log("1. Make sure you're logged into the app at http://13.58.61.189:7000");
console.log("2. Open browser console (F12)");
console.log("3. Copy and paste this entire script");
console.log("4. Run: testChatbotWithVectorDB()");
console.log("\n🚀 Ready to test!");
@

// Export the function
window.testChatbotWithVectorDB = testChatbotWithVectorDB;
