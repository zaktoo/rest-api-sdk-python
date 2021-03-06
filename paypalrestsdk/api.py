from __future__ import division

import base64
import datetime
import httplib2
import json
import logging
import os
import platform

import paypalrestsdk.util as util
from paypalrestsdk.exceptions import *
from paypalrestsdk.version import __version__


class Api:

    # User-Agent for HTTP request
    library_details = "httplib2 %s; python %s" % (httplib2.__version__, platform.python_version())
    user_agent = "PayPalSDK/rest-sdk-python %s (%s)" % (__version__, library_details)
        
    def __init__(self, options=None, **args):
        """Create API object

        Usage::

            >>> import paypalrestsdk
            >>> api = paypalrestsdk.Api(mode="sandbox", client_id='CLIENT_ID', client_secret='CLIENT_SECRET', ssl_options={})
        """
        args = util.merge_dict(options or {}, args)

        self.mode = args.get("mode", "sandbox")
        self.endpoint = args.get("endpoint", self.default_endpoint())
        self.token_endpoint = args.get("token_endpoint", self.endpoint)
        self.client_id = args["client_id"]              # Mandatory parameter, so not using `dict.get`
        self.client_secret = args["client_secret"]      # Mandatory parameter, so not using `dict.get`
        self.ssl_options = args.get("ssl_options", {})

        self.token_hash = None
        self.token_request_at = None

        if args.get("token"):
            self.token_hash = {"access_token": args["token"], "token_type": "Bearer"}

        self.options = args

    def default_endpoint(self):
        if self.mode == "live":
            return "https://api.paypal.com"
        else:
            return "https://api.sandbox.paypal.com"

    def basic_auth(self):
        """Find basic auth, and returns base64 encoded
        """
        credentials = "%s:%s" % (self.client_id, self.client_secret)
        return base64.b64encode(credentials.encode('utf-8')).decode('utf-8').replace("\n", "")

    def get_token_hash(self):
        """Generate new token by making a POST request with client credentials if 
        validate_token_hash finds token to be invalid
        """
        self.validate_token_hash()
        if self.token_hash is None:
            self.token_request_at = datetime.datetime.now()
            self.token_hash = self.http_call(util.join_url(self.token_endpoint, "/v1/oauth2/token"), "POST",
                body="grant_type=client_credentials",
                headers={
                    "Authorization": ("Basic %s" % self.basic_auth()),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json", "User-Agent": self.user_agent
                })
        
        return self.token_hash

    def validate_token_hash(self):
        """Checks if token duration has expired and if so resets token 
        """
        if self.token_request_at and self.token_hash and self.token_hash.get("expires_in") is not None:
            delta = datetime.datetime.now() - self.token_request_at
            duration = (delta.microseconds + (delta.seconds + delta.days * 24 * 3600) * 10**6) / 10**6
            if duration > self.token_hash.get("expires_in"):
                self.token_hash = None

    def get_token(self):
        return self.get_token_hash()["access_token"]

    # Get token 
    def get_token_type(self):
        """Get token type e.g. Bearer
        """
        return self.get_token_hash()["token_type"]

    def request(self, url, method, body=None, headers=None):
        """Make HTTP call, formats response and does error handling. Uses http_call method in API class.

        Usage::

            >>> api.request("https://api.sandbox.paypal.com/v1/payments/payment?count=10", "GET", {})
            >>> api.request("https://api.sandbox.paypal.com/v1/payments/payment", "POST", "{}", {} )

        """
        http_headers = util.merge_dict(self.headers(), headers or {})

        if http_headers.get('PayPal-Request-Id'):
            logging.info('PayPal-Request-Id: %s' % (http_headers['PayPal-Request-Id']))

        try:
            return self.http_call(url, method, body=body, headers=http_headers)

        # Format Error message for bad request
        except BadRequest as error:
            return {"error": json.loads(error.content)}

        # Handle Expired token
        except UnauthorizedAccess as error:
            if(self.token_hash and self.client_id):
                self.token_hash = None
                return self.request(url, method, body, headers)
            else:
                raise error

    def http_call(self, url, method, **args):
        """
        Makes a http call. Logs response information. 
        """
        logging.info('Request[%s]: %s' % (method, url))
        http = httplib2.Http(**self.ssl_options)
        start_time = datetime.datetime.now()
        response, content = http.request(url, method, **args)
        duration = datetime.datetime.now() - start_time
        logging.info('Response[%d]: %s, Duration: %s.%ss' % (response.status, response.reason, duration.seconds, duration.microseconds))
        return self.handle_response(response, content.decode('utf-8'))

    def handle_response(self, response, content):
        """Validate HTTP response
        """
        status = response.status
        if status in (301, 302, 303, 307):
            raise Redirection(response, content)
        elif 200 <= status <= 299:
            return json.loads(content) if content else {}
        elif status == 400:
            raise BadRequest(response, content)
        elif status == 401:
            raise UnauthorizedAccess(response, content)
        elif status == 403:
            raise ForbiddenAccess(response, content)
        elif status == 404:
            raise ResourceNotFound(response, content)
        elif status == 405:
            raise MethodNotAllowed(response, content)
        elif status == 409:
            raise ResourceConflict(response, content)
        elif status == 410:
            raise ResourceGone(response, content)
        elif status == 422:
            raise ResourceInvalid(response, content)
        elif 401 <= status <= 499:
            raise ClientError(response, content)
        elif 500 <= status <= 599:
            raise ServerError(response, content)
        else:
            raise ConnectionError(response, content, "Unknown response code: #{response.code}")

    def headers(self):
        """Default HTTP headers
        """
        return {
            "Authorization": ("%s %s" % (self.get_token_type(), self.get_token())),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self.user_agent
        }

    def get(self, action, headers=None):
        """Make GET request

        Usage::

            >>> api.get("v1/payments/payment?count=1")
            >>> api.get("v1/payments/payment/PAY-1234")
        """
        return self.request(util.join_url(self.endpoint, action), 'GET', headers=headers or {})

    def post(self, action, params=None, headers=None):
        """Make POST request

        Usage::

            >>> api.post("v1/payments/payment", { 'indent': 'sale' })
            >>> api.post("v1/payments/payment/PAY-1234/execute", { 'payer_id': '1234' })

        """         
        return self.request(util.join_url(self.endpoint, action), 'POST', body=json.dumps(params or {}), headers=headers or {})

    def delete(self, action, headers=None):
        """Make DELETE request
        """
        return self.request(util.join_url(self.endpoint, action), 'DELETE', headers=headers or {})

__api__ = None

def default():
    """Returns default api object and if not present creates a new one
    By default points to developer sandbox
    """
    global __api__
    if __api__ is None:
        try:
            client_id = os.environ["PAYPAL_CLIENT_ID"]
            client_secret = os.environ["PAYPAL_CLIENT_SECRET"]
        except KeyError:
            raise MissingConfig("Required PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET. Refer https://github.com/paypal/rest-api-sdk-python#configuration")

        __api__ = Api(mode=os.environ.get("PAYPAL_MODE", "sandbox"), client_id=client_id, client_secret=client_secret)
    return __api__


def set_config(options=None, **config):
    """Create new default api object with given configuration
    """
    global __api__
    __api__ = Api(options or {}, **config)
    return __api__

configure = set_config
