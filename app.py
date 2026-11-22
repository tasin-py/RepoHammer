"""RepoHammer entry point. No arguments -> desktop app; with arguments -> command line."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from hammer.cli import main
        sys.exit(main(sys.argv[1:]))
    from hammer.gui import main
    main()
