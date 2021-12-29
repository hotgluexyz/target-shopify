#!/usr/bin/env python3
import os
import json
import argparse
import logging

from pyactiveresource.connection import ResourceNotFound

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
    # Get locations
    res = shopify.GraphQL().execute("{ location { id } }")
    locations = json.loads(res)
    location = locations["data"]["location"]["id"]
    lid = location.split("gid://shopify/Location/")[1]

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

        if p.get("variants"):
            variants = []

            for v in p["variants"]:
                # Create Shopify variant
                variant = shopify.Variant()

                for key in v:
                    # Add the attributes to Shopify variant
                    setattr(variant, key, v[key])

                # Append new variant to the list
                variants.append(variant)

            # Set the variant to Shopify product
            sp.variants = variants

        # Write to shopify
        success = sp.save()

        if p.get("variants"):
            for v in p["variants"]:
                if "inventory_quantity" not in v:
                    pass

                # Get inventory_item_id
                variant = next((x for x in sp.variants if x.title == v['title']), None)
                logger.info(variant)
                iid = variant.inventory_item_id

                # Create inventory level
                il = shopify.InventoryLevel()

                il.set(lid, iid, v["inventory_quantity"])


def update_product(client, config):
    # Get input path
    input_path = f"{config['input_path']}/update_product.json"
    # Read the products
    products = load_json(input_path)
    location = shopify.Location.find()[0]
    
    for p in products:
        # Get the product
        product_id = p.get('id')
        try:
            product = shopify.Product.find(product_id)
        except ResourceNotFound:
            logger.warning(f"{product_id} is not an valid product id")
            continue

        for k in p.keys():
            if k in ['title', 'handle', 'body_html', 'vendor', 'product_type']:
                setattr(product, k, p[k])

        if not p.get("variants"):
            id = product.variants[0].id
            quantity = p["inventory_quantity"]
            p["variants"] = [{"id": id, "inventory_quantity": quantity}]
        for v in p["variants"]:
            variant_id = v.get('id')
            try:
                variant = shopify.Variant.find(variant_id)
            except ResourceNotFound:
                logger.warning(f"{variant_id} is not an valid variant id")
                continue

            for k in v.keys():
                if k in ['price', 'title']:
                    setattr(variant, k, v[k])

                if k=='inventory_quantity':
                    shopify.InventoryLevel.set(location.id, variant.inventory_item_id, v[k])
            if not variant.save():
                logger.warning(f"Error updating {variant.id} variant.")
        
        if not product.save():
            logger.warning(f"Error updating {product.id}.")


def update_inventory(client, config):
    # Get input path
    input_path = f"{config['input_path']}/update_inventory.json"
    # Read the products
    products = load_json(input_path)
    location = shopify.Location.find()[0]
    variants = shopify.Variant.find()
    
    for product in products:
        sku = product.get('sku')
        variant = [v for v in variants if v.sku==sku]
        if not variant:
            logger.info(f"{sku} is not valid.")
            continue
        variant_id = variant[0].id
        try:
            variant = shopify.Variant.find(variant_id)
        except ResourceNotFound:
            logger.warning(f"{variant_id} is not an valid variant id")
            continue

        for k in product.keys():
            if k in ['price', 'title']:
                setattr(variant, k, product[k])

            if k=='inventory_quantity':
                shopify.InventoryLevel.set(location.id, variant.inventory_item_id, product[k])
        if not variant.save():
            logger.warning(f"Error updating {variant.id} variant.")


def upload(client, config):
    # Upload Products
    if os.path.exists(f"{config['input_path']}/products.json"):
        logger.info("Found products.json, uploading...")
        upload_products(client, config)
        logger.info("products.json uploaded!")

    if os.path.exists(f"{config['input_path']}/update_product.json"):
        logger.info("Found update_product.json, uploading...")
        update_product(client, config)
        logger.info("update_product.json uploaded!")

    if os.path.exists(f"{config['input_path']}/update_inventory.json"):
        logger.info("Found update_inventory.json, uploading...")
        update_inventory(client, config)
        logger.info("update_inventory.json uploaded!")

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
