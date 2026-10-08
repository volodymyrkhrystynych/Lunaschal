"""Maintenance for an initialized DATABASE_URL; never initializes production."""
import argparse

from .maintenance import compact, rotate_epoch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest='command', required=True)
    subcommands.add_parser('rotate-epoch', help='Invalidate device cursors after a database restore')
    cleanup = subcommands.add_parser('compact', help='Retain latest records and recent changes')
    cleanup.add_argument('--keep-days', type=int, default=90)
    args = parser.parse_args()
    if args.command == 'rotate-epoch':
        print(f'Sync epoch: {rotate_epoch()}')
    else:
        print(f'Removed {compact(keep_days=args.keep_days)} historical change rows')


if __name__ == '__main__':
    main()
