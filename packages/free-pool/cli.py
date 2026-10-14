#!/usr/bin/env python3
"""CLI for free-pool"""
import os
import sys
import argparse


def serve():
    from free_pool import main
    main()


def sync():
    from sync_free_models import main
    main()


def main():
    parser = argparse.ArgumentParser(prog='free-pool')
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('serve', help='Start gateway')
    sub.add_parser('sync', help='Sync free models')
    args = parser.parse_args()
    if args.cmd == 'serve':
        serve()
    elif args.cmd == 'sync':
        sync()


if __name__ == '__main__':
    main()
