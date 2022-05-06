#!/usr/bin/env python

from setuptools import setup

setup(
    name='target-shopify',
    version='0.0.10',
    description='hotglue target for exporting data to Shopify API',
    author='hotglue',
    url='https://hotglue.xyz',
    classifiers=['Programming Language :: Python :: 3 :: Only'],
    py_modules=['target_shopify'],
    install_requires=[
        "ShopifyAPI==8.4.1",
        'argparse==1.4.0',
        'simplejson==3.17.6',
        'backoff==1.11.1'
    ],
    entry_points='''
        [console_scripts]
        target-shopify=target_shopify:main
    ''',
    packages=['target_shopify']
)
