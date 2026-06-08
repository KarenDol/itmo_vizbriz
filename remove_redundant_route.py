#!/usr/bin/env python3
"""
Script to remove the redundant patient_journey route from osaagent_routes.py
"""

def remove_redundant_route():
    """Remove the redundant patient_journey route"""
    
    # Read the file
    with open('flask_app/routes/osaagent_routes.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find the start and end of the patient_journey route
    start_marker = "@osaagent.route('/patient_journey/<int:patient_id>')"
    end_marker = "@osaagent.route('/api/stage_files/<int:patient_id>/<stage_key>', methods=['GET'])"
    
    start_pos = content.find(start_marker)
    end_pos = content.find(end_marker)
    
    if start_pos != -1 and end_pos != -1:
        # Remove the entire route
        new_content = content[:start_pos] + "# Removed redundant patient_journey route - using patient_workflow_test in main_routes.py instead\n\n" + content[end_pos:]
        
        # Write back to file
        with open('flask_app/routes/osaagent_routes.py', 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print("✅ Successfully removed redundant patient_journey route from osaagent_routes.py")
    else:
        print("❌ Could not find the patient_journey route to remove")

if __name__ == "__main__":
    remove_redundant_route() 