#!/usr/bin/env python3
"""
Simple test script for AI Tools Discovery
Run this to test the entire flow
"""

import json
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.coding_discovery_tools import AIToolsDetector

def main():
    """Test the entire discovery flow"""
    print("=" * 60)
    print("AI Tools Discovery - Full Flow Test")
    print("=" * 60)
    
    try:
        # Step 1: Create detector (uses factory)
        print("\n1. Creating detector...")
        detector = AIToolsDetector()
        print(f"   ✓ Detector created for OS: {detector.system}")
        
        # Step 2: Get device ID
        print("\n2. Extracting device ID...")
        device_id = detector.get_device_id()
        print(f"   ✓ Device ID: {device_id}")
        
        # Step 3: Detect all tools
        print("\n3. Detecting all tools...")
        tools = detector.detect_all_tools()
        print(f"   ✓ Found {len(tools)} tool(s)")
        
        for tool in tools:
            print(f"      • {tool['name']}")
            print(f"        Version: {tool.get('version', 'Unknown')}")
            print(f"        Path: {tool['install_path']}")
        
        # Step 4: Generate full report
        print("\n4. Generating complete report...")
        report = detector.generate_report()
        print(f"   ✓ Report generated")
        
        # Step 5: Show JSON report
        print("\n5. Full Report (JSON):")
        print("-" * 60)
        print(json.dumps(report, indent=2))
        print("-" * 60)
        
        # Step 6: Test specific tool detection
        print("\n6. Testing specific tool detection...")
        cursor = detector.detect_tool("Cursor")
        if cursor:
            print(f"   ✓ Cursor detected: {cursor.get('version', 'Unknown')}")
        else:
            print("   ✗ Cursor not found")
        
        claude = detector.detect_tool("Claude Code")
        if claude:
            print(f"   ✓ Claude Code detected: {claude.get('version', 'Unknown')}")
        else:
            print("   ✗ Claude Code not found")
        
        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())

