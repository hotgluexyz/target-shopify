#!/usr/bin/env python3
import os
import json
import argparse
import logging

import shopify

logger = logging.getLogger("target-shopify")
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def load_json(path):
    with open(path) as f:
        return json.load(f)


def write_json_file(filename, content):
    with open(filename, 'w') as f:
        json.dump(content, f, indent=4)


def parse_args():
    '''Parse standard command-line args.
    Parses the command-line arguments mentioned in the SPEC and the
    BEST_PRACTICES documents:
    -c,--config     Config file
    -s,--state      State file
    -d,--discover   Run in discover mode
    -p,--properties Properties file: DEPRECATED, please use --catalog instead
    --catalog       Catalog file
    Returns the parsed args object from argparse. For each argument that
    point to JSON files (config, state, properties), we will automatically
    load and parse the JSON file.
    '''
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '-c', '--config',
        help='Config file',
        required=True)

    args = parser.parse_args()
    if args.config:
        setattr(args, 'config_path', args.config)
        args.config = load_json(args.config)

    return args


def initialize_shopify_client(config):
    api_key = config.get('access_token', config.get("api_key"))
    shop = config['shop']
    version = '2021-04'
    session = shopify.Session(shop, version, api_key)
    shopify.ShopifyResource.activate_session(session)
    # Shop.current() makes a call for shop details with provided shop and api_key
    return shopify.Shop.current().attributes


def upload_products(client, config):
    # Get input path
    input_path = f"{config['input_path']}/products.json"
    # Read the products
    products = load_json(input_path)

    for p in products:
        # Create a new product
        sp = shopify.Product()

        # Title is a required field
        sp.title = p["title"]

        if p.get("product_type"):
            sp.product_type = p["product_type"]

        if p.get("body_html"):
            sp.body_html = p["body_html"]

        if p.get("vendor"):
            sp.vendor = p["vendor"]

        if p.get("tags"):
            sp.tags = p["tags"]
        
        if p.get("images"):
            sp.images = p["images"]

        # Write to shopify
        success = sp.save()


def upload(client, config):
    # Upload Products
    if os.path.exists(f"{config['input_path']}/products.json"):
        logger.info("Found products.json, uploading...")
        upload_products(client, config)
        logger.info("products.json uploaded!")

    logger.info("Posting process has completed!")


def main():
    # Parse command line arguments
    args = parse_args()
    config = args.config

    # Authorize Shopify client
    client = initialize_shopify_client(config)

    # Upload the Shopify data
    upload(client, config)


if __name__ == "__main__":
    main()
