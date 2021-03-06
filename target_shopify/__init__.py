#!/usr/bin/env python3
import os
import json
import sys
import argparse
import logging
import backoff
import simplejson
import math

from pyactiveresource.connection import ResourceNotFound
import pyactiveresource

import shopify

logging.getLogger('backoff').setLevel(logging.CRITICAL)
logger = logging.getLogger("target-shopify")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

MAX_RETRIES = 5

def load_json(path):
    with open(path) as f:
        return json.load(f)


def write_json_file(filename, content):
    with open(filename, 'w') as f:
        json.dump(content, f, indent=4)


def is_not_status_code_fn(status_code):
    def gen_fn(exc):
        if getattr(exc, 'code', None) and exc.code not in status_code:
            return True
        # Retry other errors up to the max
        return False
    return gen_fn


def leaky_bucket_handler(details):
    logger.info("Received 429 -- sleeping for %s seconds",
                details['wait'])


def retry_handler(details):
    logger.info("Received 500 or retryable -- Retry %s/%s",
                details['tries'], MAX_RETRIES)


def retry_after_wait_gen(**kwargs):
    # This is called in an except block so we can retrieve the exception
    # and check it.
    exc_info = sys.exc_info()
    resp = exc_info[1].response
    # Retry-After is an undocumented header. But honoring
    # it was proven to work in our spikes.
    # It's been observed to come through as lowercase, so fallback if not present
    sleep_time_str = resp.headers.get('Retry-After', resp.headers.get('retry-after'))
    yield math.floor(float(sleep_time_str))


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


@backoff.on_exception(backoff.expo,
                        (pyactiveresource.connection.ServerError,
                        pyactiveresource.formats.Error,
                        simplejson.scanner.JSONDecodeError,
                        Exception),
                        on_backoff=retry_handler,
                        max_tries=MAX_RETRIES)
@backoff.on_exception(retry_after_wait_gen,
                        pyactiveresource.connection.ClientError,
                        giveup=is_not_status_code_fn([429]),
                        on_backoff=leaky_bucket_handler)
def insert_record(obj):
    return obj.save()


def get_variant_by_sku(sku):
    gql_client = shopify.GraphQL()
    gql_query = "query productVariants($query:String!){productVariants(first:1, query:$query){edges{node{id}}}}"
    response = gql_client.execute(gql_query, dict(query=f"sku:{sku}"))
    response = json.loads(response)
    gid = response["data"]["productVariants"]["edges"][0]["node"]["id"]
    return gid.split("/")[-1]


def upload_orders(client, config):
    # Get input path
    input_path = f"{config['input_path']}/orders.json"
    # Read the orders
    orders = load_json(input_path)

    for o in orders:
        # Create a new order
        so = shopify.Order()
        lines = []

        # Get line items
        for li in o["line_items"]:
            
            variant = li.get("variant_id")
            
            if not variant:
                # Get SKU
                sku = li["sku"]
                # Get matching variant
                try:
                    variant = get_variant_by_sku(sku)
                except:
                    logger.info(f"{sku} is not valid.")
                    continue

            sl = shopify.LineItem()
            # Set variant id
            sl.variant_id = variant
            # Set quantity
            sl.quantity = li["quantity"]

            lines.append(sl)

        # Save line items
        so.line_items = lines

        # Write to shopify
        if not insert_record(so):
            logger.warning(f"Failed creating order.")


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
        success = insert_record(sp)

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
            if not insert_record(variant):
                logger.warning(f"Failed on updating {variant.id} variant.")
        
        if not insert_record(product):
            logger.warning(f"Failed on updating {product.id}.")


def update_inventory(client, config):
    # Get input path
    input_path = f"{config['input_path']}/update_inventory.json"
    # Read the products
    products = load_json(input_path)
    
    for product in products:
        variant_id = product.get('variant_id')
        location_id = product.get('location_id')
        try:
            variant = shopify.Variant.find(variant_id)
        except ResourceNotFound:
            logger.warning(f"{variant_id} is not an valid variant id")
            continue

        for k in product.keys():
            if k in ['price', 'title']:
                setattr(variant, k, product[k])

            if k=='inventory_quantity':
                response = shopify.InventoryLevel.adjust(location_id, variant.inventory_item_id, product[k])
                logger.info(f"Variant: {variant_id} at location: {variant_id} updated at {response.updated_at}")
        if not insert_record(variant):
            logger.warning(f"Failed on updating variant: {variant_id}")


def update_fulfillments(client, config):
    # Get input path
    input_path = f"{config['input_path']}/update_fulfillments.json"
    # Read the products
    fulfillments = load_json(input_path)

    for fulfillment in fulfillments:
        ff = shopify.Fulfillment.find(order_id=fulfillment.get("order_id"))
        for item in ff:
            if item.status!="cancelled":
                ff_event = shopify.FulfillmentEvent({'order_id': item.order_id, 'fulfillment_id': item.id})
                ff_event.status = fulfillment.get("shipment_status")
                if not insert_record(ff_event):
                    logger.warning(f"Failed on updating {item.id} fulfillment.")


def fulfill_order(client, config):
    # Get input path
    input_path = f"{config['input_path']}/fulfill_order.json"
    # Read the products
    fulfillments = load_json(input_path)

    for fulfillment in fulfillments:
        ff = shopify.Fulfillment(fulfillment)
        if not insert_record(ff):
            logger.warning(f"Failed on updating {fulfillment.order_id} fulfillment.")


def upload_refunds(client, config):
    # Get input path
    input_path = f"{config['input_path']}/refunds.json"

    # Read the refunds
    refunds = load_json(input_path)
    for refund in refunds:

        if "refund_line_items" not in refund:
            refund["refund_line_items"] = []
        if "shipping" not in refund:
            refund["shipping"] = None   
        ro = shopify.Refund(refund)
        refund_calculations = shopify.Refund.calculate(order_id=refund["order_id"],refund_line_items = refund["refund_line_items"],shipping=refund["shipping"])
        refund_calculations = refund_calculations.__dict__
        shipping = refund_calculations["attributes"]["shipping"].__dict__["attributes"]
        currency = refund_calculations["attributes"]["currency"]
        transactions_calculated = refund_calculations["attributes"]["transactions"]
        transactions = []
        for transaction in transactions_calculated:
            t = transaction.__dict__["attributes"]
            t["amount"] = t["maximum_refundable"]
            t["kind"] = "refund"
            transactions.append(t)
        shipping["amount"] = shipping["maximum_refundable"]   
        refund_payload = {"order_id":refund["order_id"],"currency":currency,"shipping":shipping,"transactions":transactions}
        refund_payload = shopify.Refund(refund_payload)
        if not insert_record(refund_payload):
            logger.warning(f"Failed on uploading refund for order ID: {refund['order_id']} .")


def upload(client, config):

    # Create Fulfillment
    if os.path.exists(f"{config['input_path']}/fulfill_order.json"):
        logger.info("Found fulfill_order.json, uploading...")
        fulfill_order(client, config)
        logger.info("fulfill_order.json uploaded!")

    # Update Fulfillment
    if os.path.exists(f"{config['input_path']}/update_fulfillments.json"):
        logger.info("Found update_fulfillments.json, uploading...")
        update_fulfillments(client, config)
        logger.info("update_fulfillments.json uploaded!")

    # Upload Products
    if os.path.exists(f"{config['input_path']}/products.json"):
        logger.info("Found products.json, uploading...")
        upload_products(client, config)
        logger.info("products.json uploaded!")

    # Upload Orders
    if os.path.exists(f"{config['input_path']}/orders.json"):
        logger.info("Found orders.json, uploading...")
        upload_orders(client, config)
        logger.info("orders.json uploaded!")

    if os.path.exists(f"{config['input_path']}/update_product.json"):
        logger.info("Found update_product.json, uploading...")
        update_product(client, config)
        logger.info("update_product.json uploaded!")

    if os.path.exists(f"{config['input_path']}/update_inventory.json"):
        logger.info("Found update_inventory.json, uploading...")
        update_inventory(client, config)
        logger.info("update_inventory.json uploaded!")

    # Upload refunds
    if os.path.exists(f"{config['input_path']}/refunds.json"):
        logger.info("Found refunds.json, uploading...")
        upload_refunds(client, config)
        logger.info("refunds.json uploaded!")    

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
