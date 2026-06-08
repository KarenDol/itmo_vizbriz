#!/usr/bin/env python3
"""
Image Library - Centralized image management system
Stores all image names and paths in one place
"""

import os
from flask import url_for

class ImageLibrary:
    """Centralized image library for the application"""
    
    # Base paths
    STATIC_DIR = "flask_app/flask_static"
    IMAGES_DIR = "images"
    LOGOS_DIR = "images/logos"
    BRANDING_DIR = "branding"
    
    # Default/fallback images
    DEFAULT_LOGO = "images/default-logo.png"
    DEFAULT_AVATAR = "images/default-avatar.png"
    DEFAULT_CLINIC = "images/default-clinic.jpg"
    
    # DSO Logos
    DSO_LOGOS = {
        'heartland_dental': 'images/logos/heartland_dental.png',
        'pacific_dental': 'images/logos/pacific_dental.png',
        'aspen_dental': 'images/logos/aspen_dental.png',
        'smile_brands': 'images/logos/smile_brands.png',
        'dental_care_alliance': 'images/logos/dental_care_alliance.png',
        'roligo_dental': 'images/logos/roligo_logo.jpg',
        'independent_practice': 'images/logos/independent.png',
    }
    
    # Clinic/Practice Logos
    CLINIC_LOGOS = {
        'default_clinic': 'images/clinics/default_clinic.png',
        'smile_dental': 'images/clinics/smile_dental.png',
        'family_dentistry': 'images/clinics/family_dentistry.png',
        'bright_smiles': 'images/clinics/bright_smiles.png',
    }
    
    # Branding/App Images
    BRANDING = {
        'app_logo': 'branding/vizbrizz_logo_color.svg',
        'app_logo_white': 'branding/vizbrizz_logo_color_white_long.png',
        'dr_briz': 'branding/Dr Briz.svg',
        'agent_style': 'branding/agent_style.css',
    }
    
    # Icons and UI elements
    ICONS = {
        'favicon': 'favicon.ico',
        'android_192': 'android-chrome-192x192.png',
        'android_512': 'android-chrome-512x512.png',
        'apple_touch': 'apple-touch-icon.png',
        'favicon_16': 'favicon-16x16.png',
        'favicon_32': 'favicon-32x32.png',
    }
    
    # Medical/Dental related images
    MEDICAL = {
        'patient_barcode': 'images/patient_barcode.png',
        'dental_xray': 'images/dental_xray.jpg',
        'cbct_scan': 'images/cbct_scan.jpg',
        'sleep_study': 'images/sleep_study.jpg',
    }
    
    @classmethod
    def get_dso_logo(cls, dso_name):
        """Get DSO logo path by name"""
        # Normalize DSO name for lookup
        key = dso_name.lower().replace(' ', '_').replace('-', '_')
        return cls.DSO_LOGOS.get(key, cls.DEFAULT_LOGO)
    
    @classmethod
    def get_clinic_logo(cls, clinic_name):
        """Get clinic logo path by name"""
        # Normalize clinic name for lookup
        key = clinic_name.lower().replace(' ', '_').replace('-', '_')
        return cls.CLINIC_LOGOS.get(key, cls.DEFAULT_CLINIC)
    
    @classmethod
    def get_branding_image(cls, image_name):
        """Get branding image path"""
        return cls.BRANDING.get(image_name, cls.DEFAULT_LOGO)
    
    @classmethod
    def get_icon(cls, icon_name):
        """Get icon path"""
        return cls.ICONS.get(icon_name, cls.DEFAULT_LOGO)
    
    @classmethod
    def get_medical_image(cls, image_name):
        """Get medical/dental image path"""
        return cls.MEDICAL.get(image_name, cls.DEFAULT_LOGO)
    
    @classmethod
    def add_dso_logo(cls, dso_name, image_path):
        """Add a new DSO logo to the library"""
        key = dso_name.lower().replace(' ', '_').replace('-', '_')
        cls.DSO_LOGOS[key] = image_path
    
    @classmethod
    def list_all_images(cls):
        """List all images in the library"""
        all_images = {}
        all_images.update(cls.DSO_LOGOS)
        all_images.update(cls.CLINIC_LOGOS)
        all_images.update(cls.BRANDING)
        all_images.update(cls.ICONS)
        all_images.update(cls.MEDICAL)
        return all_images
    
    @classmethod
    def check_image_exists(cls, image_path):
        """Check if image file exists on disk"""
        full_path = os.path.join(cls.STATIC_DIR, image_path)
        return os.path.exists(full_path)
    
    @classmethod
    def get_url(cls, image_path):
        """Convert image path to Flask URL (when in Flask context)"""
        try:
            return url_for('static', filename=image_path)
        except:
            # If not in Flask context, return relative path
            return f"/static/{image_path}"

# Convenience functions for common use cases
def get_dso_logo(dso_name):
    """Quick function to get DSO logo"""
    return ImageLibrary.get_dso_logo(dso_name)

def get_clinic_logo(clinic_name):
    """Quick function to get clinic logo"""
    return ImageLibrary.get_clinic_logo(clinic_name)

def get_app_logo():
    """Quick function to get main app logo"""
    return ImageLibrary.get_branding_image('app_logo')

# Image library configuration for easy updates
IMAGE_LIBRARY_CONFIG = {
    "dso_logos": ImageLibrary.DSO_LOGOS,
    "clinic_logos": ImageLibrary.CLINIC_LOGOS,
    "branding": ImageLibrary.BRANDING,
    "icons": ImageLibrary.ICONS,
    "medical": ImageLibrary.MEDICAL,
    "defaults": {
        "logo": ImageLibrary.DEFAULT_LOGO,
        "avatar": ImageLibrary.DEFAULT_AVATAR,
        "clinic": ImageLibrary.DEFAULT_CLINIC,
    }
}

if __name__ == "__main__":
    # Test/demo the image library
    print("🖼️  Image Library Contents:")
    print("="*50)
    
    print("\n📁 DSO Logos:")
    for name, path in ImageLibrary.DSO_LOGOS.items():
        print(f"  {name}: {path}")
    
    print("\n🏥 Clinic Logos:")
    for name, path in ImageLibrary.CLINIC_LOGOS.items():
        print(f"  {name}: {path}")
    
    print("\n🎨 Branding Images:")
    for name, path in ImageLibrary.BRANDING.items():
        print(f"  {name}: {path}")
    
    print("\n🔍 Testing DSO logo lookup:")
    test_dsos = ["Heartland Dental", "Pacific Dental Services", "Unknown DSO"]
    for dso in test_dsos:
        logo = ImageLibrary.get_dso_logo(dso)
        print(f"  '{dso}' -> {logo}")
    
    print("\n📋 All images:")
    all_images = ImageLibrary.list_all_images()
    print(f"  Total images: {len(all_images)}") 