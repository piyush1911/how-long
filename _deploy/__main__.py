'''Deployment script for DoIStillHaveAJob

This script will deploy the DoIStillHaveAJob web site in a new resource group.
After deployment, the script will offer to open the site, and then delete all
resources associated with it.

Before running this script, see the section and link below on providing
credentials to your Azure account.
'''

__author__ = "Steve Dower <steve.dower@microsoft.com>"
__version__ = "1.0.0"

import json
import os
import pathlib
import sys
import uuid

#################################################
#region Credential Boilerplate

# See http://azure-sdk-for-python.readthedocs.org/en/latest/resourcemanagementauthentication.html
# for info about setting CREDENTIALS

from azure.common.credentials import UserPassCredentials
try:
    CREDENTIALS = UserPassCredentials(
        '<Azure Active Directory username>',
        '<password>',
    )
except:
    CREDENTIALS = None

# SUBSCRIPTION_ID should be a subscription that can be accessed with the
# above credentials. If not provided, a list of available subscriptions will
# be displayed and the script will exit.
SUBSCRIPTION_ID = ''

try:
    # Credentials may be kept in a separate file outside of source control.
    from _deploy.deploy_credentials import CREDENTIALS, SUBSCRIPTION_ID
except ImportError:
    pass

if not CREDENTIALS:
    print("The provided credentials were invalid.", file=sys.stderr)
    print("Review deploy.py and update deployment settings as necessary.", file=sys.stderr)
    sys.exit(1)

if not SUBSCRIPTION_ID:
    # Display a list of available subscriptions if SUBSCRIPTION_ID was not given

    from azure.mgmt.resource.subscriptions import SubscriptionClient
    sc = SubscriptionClient(CREDENTIALS)
    print('SUBSCRIPTION_ID was not provided. Select an id from the following list.')
    for sub in sc.subscriptions.list():
        print('    {}: {}'.format(sub.subscription_id, sub.display_name))
    sys.exit(1)

#endregion
#################################################

from azure.mgmt.resource.resources import ResourceManagementClient
from azure.mgmt.resource.resources.models import ResourceGroup, DeploymentProperties, DeploymentMode

from _deploy.deploy_helpers import get_package, print_operation_results, Site

#################################################
# Constants for this deployment.
#
# Some names include random UUIDs to avoid collisions.
# These are not necessary in controlled environments.

if os.path.isfile('_last_deploy.json') and '--full' not in sys.argv:
    print('Reusing parameters from _last_deploy.json')
    with open('_last_deploy.json', 'r') as f:
        params = json.load(f)
else:
    print('Generating new deployment parameters')
    params = {
        'RESOURCE_GROUP': "demo" + uuid.uuid4().hex,
        'DEPLOYMENT': "ContosoInternalApps",
        'LOCATION': "West US",

        'COMPANY_NAME': "Contoso",
        'WEBSITE': "HowLong" + uuid.uuid4().hex,

        'DATABASE_ADMIN_USER': "contosodb",
        'DATABASE_ADMIN_PASS': "PW-" + uuid.uuid4().hex,
        'DATABASE_NAME': "howlong",
    }

    with open('_last_deploy.json', 'w') as f:
        json.dump(params, f)

RESOURCE_GROUP = params['RESOURCE_GROUP']
DEPLOYMENT = params['DEPLOYMENT']
LOCATION = params['LOCATION']

COMPANY_NAME = params['COMPANY_NAME']
WEBSITE = params['WEBSITE']

DATABASE_ADMIN_USER = params['DATABASE_ADMIN_USER']
DATABASE_ADMIN_PASS = params['DATABASE_ADMIN_PASS']
DATABASE_NAME = params['DATABASE_NAME']

DEPLOY_ROOT = pathlib.Path(__file__).absolute().parent.parent

def get_deploy_files():
    return [(s, d) for s, d in [
        *get_package('app'),
        *get_package('HowLong'),
        *get_package('static'),
        *get_package('wheelhouse'),
        (DEPLOY_ROOT / 'requirements.txt', 'requirements.txt'),
        (DEPLOY_ROOT / 'manage.py', 'manage.py'),
        (DEPLOY_ROOT / 'create_test_data.py', 'create_test_data.py'),
        (DEPLOY_ROOT / 'web.config', 'web.config'),
        (DEPLOY_ROOT / 'static.web.config', 'static\\web.config'),
    ] if '__pycache__' not in s.parts]

#################################################
# Create management clients

rc = ResourceManagementClient(credentials=CREDENTIALS, subscription_id=SUBSCRIPTION_ID)

#################################################
# Create a resource group
#
# A resource group contains our entire deployment
# and makes it easy to manage related services.

print("Creating resource group:", RESOURCE_GROUP)

rc.resource_groups.create_or_update(RESOURCE_GROUP, ResourceGroup(location=LOCATION))

try:

    #################################################
    # Create a resource manager template
    #
    # The template defines our entire service, including
    # the storage account and website. After deployment
    # is complete, our site is ready to use.
    #
    # Available arguments for templates can be found
    # at http://aka.ms/arm-template and http://resources.azure.com

    print("Deploying:", DEPLOYMENT)

    # TEMPLATE is read from an associated deploy.json file.
    with open(str(DEPLOY_ROOT / '_deploy' / 'deploy.json'), 'r', encoding='utf-8') as f:
        TEMPLATE = json.load(f)

    # PARAMETERS will be merged with TEMPLATE on the server to produce
    # our specific deployment. This allows templates to be reused without
    # modification.
    PARAMETERS = {
        "companyName": { "value": COMPANY_NAME },
        "siteName": { "value": WEBSITE },
        "dbAdminUser": { "value": DATABASE_ADMIN_USER },
        "dbAdminPassword": { "value": DATABASE_ADMIN_PASS },
        "dbName": { "value": DATABASE_NAME },
        "hostingPlanName": { "value": DEPLOYMENT }
    }

    result = rc.deployments.validate(
        RESOURCE_GROUP,
        DEPLOYMENT,
        properties=DeploymentProperties(
            mode=DeploymentMode.incremental,
            template=TEMPLATE,
            parameters=PARAMETERS,
        )
    )

    if result.error:
        print('''Validation failed: {0.code}
    Target: {0.target}

    {0.message}

    {0.details}
    '''.format(result.error))
        sys.exit(1)

    rc.deployments.create_or_update(
        RESOURCE_GROUP,
        DEPLOYMENT,
        properties=DeploymentProperties(
            mode=DeploymentMode.incremental,
            template=TEMPLATE,
            parameters=PARAMETERS,
        )
    ).result()
    
    print_operation_results(rc, RESOURCE_GROUP, DEPLOYMENT)

    #################################################
    # Update our website's files

    site = Site(CREDENTIALS, SUBSCRIPTION_ID, RESOURCE_GROUP, WEBSITE)
    
    print('Uploading source files')
    site.upload_files(get_deploy_files(), 'site/wwwroot')
    print('Success')
    print()

    print('Installing packages')
    print(site.exec(r"D:\home\Python35\python.exe -m pip install --disable-pip-version-check -r requirements.txt") or 'Success')
    print()

    print('Collecting static files')
    print(site.exec(r"D:\home\Python35\python.exe D:\home\site\wwwroot\manage.py collectstatic --noinput") or 'Success')
    print()

    print('Migrating Django DB')
    print(site.exec(r"D:\home\Python35\python.exe D:\home\site\wwwroot\manage.py migrate") or 'Success')
    print()

    #################################################
    # Navigate to the site

    host_names = list(site.host_names)
    print()
    print('Site is available at:')
    for name in host_names:
        print('   ', name)

    result = rc.resources.get(
        RESOURCE_GROUP,
        "Microsoft.Sql",
        "servers",
        "", "", "2014-04-01",
        raw=True
    ).response.json()
    
    print()
    print('Databases are available at:')
    for db in result['value']:
        print('  ', db['properties'].get('fullyQualifiedDomainName') or db['name'])
        print('    User:', db['properties'].get('administratorLogin') or '(unknown)')
        print('    Pass:', DATABASE_ADMIN_PASS)
    print()

    if 'y' in input("Browse to {}? [y/N] ".format(host_names[0])).lower():
        import webbrowser
        webbrowser.open(host_names[0])

finally:
    #################################################
    # Delete the resource group
    #
    # This quickly cleans up all of our resources.

    print()
    if 'y' in input("Delete resource group? [y/N] ").lower():
        print('Deleting resource group:', RESOURCE_GROUP)
        rc.resource_groups.delete(RESOURCE_GROUP).result()
