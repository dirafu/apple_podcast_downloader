import sys
import argparse
from . import config
from . import dl
from . import fetch

def main():
    parser = argparse.ArgumentParser(
        description="Archive Apple-hosted (HLS/Widevine) podcasts and exclusive episodes.",
        epilog="Use 'apple-podcast <command> -h' for help on a specific command."
    )
    
    parser.add_argument(
        "--env-file", 
        help="Path to a custom .env file", 
        default=None
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")

    subparsers.add_parser(
        "configure", 
        help="Interactively set up authentication tokens and device path",
        add_help=False
    )
    
    subparsers.add_parser(
        "dl", 
        help="Download a single episode using its m3u8 URL",
        add_help=False
    )
    
    subparsers.add_parser(
        "fetch", 
        help="Download a show or an episode by its podcasts.apple.com page URL",
        add_help=False
    )

    # Parse known args, so we can leave subcommand args untouched
    args, remaining = parser.parse_known_args()

    # Load environment variables BEFORE running any commands
    config.load_env(args.env_file)

    if args.command == "configure":
        config.configure_cli()
    elif args.command == "dl":
        sys.argv = [sys.argv[0]] + remaining
        dl._cli()
    elif args.command == "fetch":
        sys.argv = [sys.argv[0]] + remaining
        fetch.main()

if __name__ == "__main__":
    main()
