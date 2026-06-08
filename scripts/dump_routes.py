#!/usr/bin/env python3
"""
Script to dump all Flask routes for backward compatibility verification.
This script will be used to ensure that any refactoring maintains all existing routes.
"""

import os
import sys
import json
from collections import defaultdict

# Add the flask_app directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def dump_routes():
    """Dump all routes from the Flask application."""
    try:
        from flask_app import create_app
        
        app = create_app()
        
        routes = []
        for rule in app.url_map.iter_rules():
            route_info = {
                'endpoint': rule.endpoint,
                'methods': sorted(list(rule.methods - {'HEAD', 'OPTIONS'})),  # Exclude auto-generated methods
                'rule': str(rule),
                'arguments': list(rule.arguments),
                'defaults': rule.defaults
            }
            routes.append(route_info)
        
        # Sort routes for consistent output
        routes.sort(key=lambda x: (x['rule'], x['endpoint']))
        
        print("=== FLASK ROUTES DUMP ===")
        print(f"Total routes: {len(routes)}")
        print()
        
        # Group by blueprint
        blueprints = defaultdict(list)
        for route in routes:
            blueprint_name = route['endpoint'].split('.')[0] if '.' in route['endpoint'] else 'main'
            blueprints[blueprint_name].append(route)
        
        for blueprint, blueprint_routes in sorted(blueprints.items()):
            print(f"=== {blueprint.upper()} BLUEPRINT ({len(blueprint_routes)} routes) ===")
            for route in blueprint_routes:
                methods_str = ', '.join(route['methods'])
                print(f"{route['rule']:<50} {methods_str:<20} {route['endpoint']}")
            print()
        
        # Save to JSON file for comparison
        output_file = os.path.join(os.path.dirname(__file__), 'routes_dump.json')
        with open(output_file, 'w') as f:
            json.dump(routes, f, indent=2, sort_keys=True)
        
        print(f"Routes saved to: {output_file}")
        
        return routes
        
    except Exception as e:
        print(f"Error dumping routes: {e}")
        import traceback
        traceback.print_exc()
        return []

def compare_routes(old_routes_file, new_routes):
    """Compare old and new route dumps."""
    try:
        with open(old_routes_file, 'r') as f:
            old_routes = json.load(f)
        
        # Create sets for comparison (ignore order)
        old_set = set()
        new_set = set()
        
        for route in old_routes:
            key = (route['rule'], tuple(sorted(route['methods'])), route['endpoint'])
            old_set.add(key)
        
        for route in new_routes:
            key = (route['rule'], tuple(sorted(route['methods'])), route['endpoint'])
            new_set.add(key)
        
        # Find differences
        added = new_set - old_set
        removed = old_set - new_set
        
        print("=== ROUTE COMPARISON ===")
        if not added and not removed:
            print("✅ All routes match! No changes detected.")
        else:
            if added:
                print(f"❌ Added routes ({len(added)}):")
                for rule, methods, endpoint in sorted(added):
                    print(f"  + {rule} {list(methods)} {endpoint}")
            
            if removed:
                print(f"❌ Removed routes ({len(removed)}):")
                for rule, methods, endpoint in sorted(removed):
                    print(f"  - {rule} {list(methods)} {endpoint}")
        
        return len(added) == 0 and len(removed) == 0
        
    except FileNotFoundError:
        print(f"Old routes file not found: {old_routes_file}")
        return False
    except Exception as e:
        print(f"Error comparing routes: {e}")
        return False

if __name__ == '__main__':
    routes = dump_routes()
    
    # If a comparison file is provided as argument
    if len(sys.argv) > 1:
        old_file = sys.argv[1]
        compare_routes(old_file, routes)
