#!/usr/bin/env python3
"""
migrate_gsi_references.py - Find and optionally update GSI references in code

Usage:
    # Dry run (just show what would change)
    python migrate_gsi_references.py /path/to/src/

    # Actually update files
    python migrate_gsi_references.py /path/to/src/ --apply

    # Check specific file
    python migrate_gsi_references.py /path/to/src/admin_orders.py
"""

import os
import sys
import re
from pathlib import Path
from typing import List, Tuple

OLD_INDEX = "client-created-index"
NEW_INDEX = "client-created-v2-index"

def find_gsi_references(filepath: str) -> List[Tuple[int, str, str]]:
    """
    Find lines that reference the old GSI.
    Returns: [(line_number, old_line, new_line), ...]
    """
    changes = []
    
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines, 1):
        if OLD_INDEX in line:
            # Create the new line with updated index name
            new_line = line.replace(OLD_INDEX, NEW_INDEX)
            changes.append((i, line.rstrip(), new_line.rstrip()))
    
    return changes

def apply_changes(filepath: str, changes: List[Tuple[int, str, str]]) -> None:
    """Apply the changes to the file"""
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Apply changes (work backwards to maintain line numbers)
    for line_num, old_line, new_line in sorted(changes, reverse=True):
        lines[line_num - 1] = new_line + '\n'
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(lines)

def scan_directory(directory: str, extensions: List[str] = ['.py', '.yaml', '.yml']) -> dict:
    """Scan directory for files with GSI references"""
    results = {}
    
    for root, dirs, files in os.walk(directory):
        # Skip common non-code directories
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules', '__pycache__', 'venv', '.aws-sam']]
        
        for file in files:
            if any(file.endswith(ext) for ext in extensions):
                filepath = os.path.join(root, file)
                changes = find_gsi_references(filepath)
                if changes:
                    results[filepath] = changes
    
    return results

def print_results(results: dict) -> None:
    """Pretty print the results"""
    if not results:
        print("✅ No references to old GSI found!")
        return
    
    print(f"\n🔍 Found references to '{OLD_INDEX}' in {len(results)} file(s):\n")
    
    for filepath, changes in results.items():
        print(f"📄 {filepath}")
        print(f"   {len(changes)} change(s) needed:\n")
        
        for line_num, old_line, new_line in changes:
            print(f"   Line {line_num}:")
            print(f"   ❌ {old_line}")
            print(f"   ✅ {new_line}")
            print()
    
    print(f"\n📊 Summary: {sum(len(changes) for changes in results.values())} total changes in {len(results)} files")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    path = sys.argv[1]
    apply = '--apply' in sys.argv
    
    if not os.path.exists(path):
        print(f"❌ Error: Path does not exist: {path}")
        sys.exit(1)
    
    # Scan for references
    if os.path.isfile(path):
        results = {path: find_gsi_references(path)}
    else:
        results = scan_directory(path)
    
    # Print results
    print_results(results)
    
    if not results:
        sys.exit(0)
    
    # Apply changes if requested
    if apply:
        print("\n⚠️  Applying changes...")
        for filepath, changes in results.items():
            apply_changes(filepath, changes)
            print(f"✅ Updated: {filepath}")
        print(f"\n✅ Successfully updated {len(results)} file(s)!")
        print(f"\n💡 Next steps:")
        print(f"   1. Review the changes: git diff")
        print(f"   2. Test your application")
        print(f"   3. Deploy: sam build && sam deploy")
    else:
        print(f"\n💡 To apply these changes, run:")
        print(f"   python {sys.argv[0]} {path} --apply")
        print(f"\n⚠️  Remember to review and test before deploying!")

if __name__ == "__main__":
    main()