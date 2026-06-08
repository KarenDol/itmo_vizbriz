#!/usr/bin/env python3
"""
Migration: Create labs and lab_references tables and seed Or-Hashen with mock email/phone.
Run from project root: python migrations/create_labs_and_lab_references.py
"""
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run_migration():
    from flask_app import create_app
    from flask_app.extensions import db
    from flask_app.models import Lab

    app = create_app()
    with app.app_context():
        db.create_all()  # Only creates tables that don't exist
        if Lab.query.filter_by(name='Or-Hashen').first() is None:
            lab = Lab(
                name='Or-Hashen',
                email='lab@or-hashen.example',
                phone='+972-00-0000000',
                address=None,
                website=None
            )
            db.session.add(lab)
            db.session.commit()
            print('Created Or-Hashen lab with mock email and phone.')
        else:
            print('Or-Hashen lab already exists.')

if __name__ == '__main__':
    run_migration()
