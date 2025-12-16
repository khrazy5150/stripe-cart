import os
import json
import boto3
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Global cache
_config_cache = {}
_cache_timestamp = None
_cache_ttl_seconds = 300  # 5 minutes

dynamodb = boto3.resource('dynamodb')

def get_config_table():
    """Get the app-config DynamoDB table."""
    table_name = os.environ.get('APP_CONFIG_TABLE', 'app-config')
    logger.info(f"Config Table: {table_name}")
    return dynamodb.Table(table_name)

def get_environment():
    """Get current environment (dev, prod, staging)."""
    return os.environ.get('ENVIRONMENT', 'dev')

def _is_cache_valid():
    """Check if cache is still valid."""
    if not _cache_timestamp:
        return False
    elapsed = (datetime.now() - _cache_timestamp).total_seconds()
    return elapsed < _cache_ttl_seconds

def load_config(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Load configuration from DynamoDB with caching.
    
    Configuration precedence:
    1. Environment-specific value (e.g., dev, prod)
    2. Global value
    3. Default value (if provided in code)
    """
    global _config_cache, _cache_timestamp
    
    # Return cached config if valid
    if not force_refresh and _is_cache_valid() and _config_cache:
        logger.debug("Returning cached config")
        return _config_cache
    
    try:
        table = get_config_table()
        env = get_environment()
        
        # Scan all config items
        response = table.scan()
        items = response.get('Items', [])
        
        # Continue scanning if paginated
        while 'LastEvaluatedKey' in response:
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))
        
        # Build config dictionary with precedence
        config = {}
        
        for item in items:
            key = item['config_key']
            item_env = item.get('environment', 'global')
            value = item['value']
            
            # Environment-specific values override global
            if item_env == env:
                config[key] = value
            elif item_env == 'global' and key not in config:
                config[key] = value
        
        # Update cache
        _config_cache = config
        _cache_timestamp = datetime.now()
        
        logger.info(f"Loaded {len(config)} config values for environment: {env}")
        return config
        
    except Exception as e:
        logger.error(f"Failed to load config: {str(e)}")
        # Return cached config if available, even if expired
        if _config_cache:
            logger.warning("Using expired cache due to load failure")
            return _config_cache
        # Return empty dict as fallback
        return {}

def get_config_value(key: str, default: Any = None, force_refresh: bool = False) -> Any:
    """
    Get a single configuration value.
    
    Args:
        key: Configuration key
        default: Default value if key not found
        force_refresh: Force reload from DynamoDB
    
    Returns:
        Configuration value or default
    """
    config = load_config(force_refresh)
    return config.get(key, default)

def get_api_url(service: str) -> str:
    """
    Get API base URL for a service.
    
    Args:
        service: Service name (stripe, shippo, easypost, etc.)
    
    Returns:
        Base URL for the service
    """
    key = f"{service}_api_base_url"
    
    # Defaults for common services
    defaults = {
        'stripe_api_base_url': 'https://api.stripe.com',
        'shippo_api_base_url': 'https://api.goshippo.com',
        'easypost_api_base_url': 'https://api.easypost.com/v2',
        'shipstation_api_base_url': 'https://ssapi.shipstation.com',
        'easyship_api_base_url': 'https://api.easyship.com/2023-01'
    }
    
    return get_config_value(key, defaults.get(key, ''))

def get_api_timeout(service: str = 'default') -> int:
    """
    Get API timeout for a service.
    
    Args:
        service: Service name or 'default'
    
    Returns:
        Timeout in seconds
    """
    key = f"{service}_api_timeout"
    default_timeout = get_config_value('default_api_timeout', 10)
    return int(get_config_value(key, default_timeout))

def get_frontend_url(path_type: str = 'base') -> str:
    """
    Get frontend URL.
    
    Args:
        path_type: 'base', 'test_dir', 'prod_dir'
    
    Returns:
        URL or path component
    """
    key = f"frontend_{path_type}_url"
    
    defaults = {
        'frontend_base_url': 'https://juniorbay.com',
        'frontend_test_dir': 'test',
        'frontend_prod_dir': 'dist'
    }
    
    return get_config_value(key, defaults.get(key, ''))

def build_offer_url(client_id: str, offer_name: str, test_mode: bool = False) -> str:
    """
    Build complete offer URL based on configuration.
    
    Args:
        client_id: Tenant client ID
        offer_name: Offer name/path
        test_mode: Whether to use test or prod directory
    
    Returns:
        Complete offer URL
    """
    base = get_frontend_url('base')
    dir_type = 'test_dir' if test_mode else 'prod_dir'
    directory = get_frontend_url(dir_type)
    
    # Format: https://juniorbay.com/{test|dist}/{clientID}/{offer_name}/
    return f"{base}/{directory}/{client_id}/{offer_name}/"

def get_ses_config() -> Dict[str, Any]:
    """
    Get SES email configuration.
    
    Returns:
        Dictionary with SES settings
    """
    return {
        'region': get_config_value('ses_region', 'us-west-2'),
        'from_email': get_config_value('ses_from_email', 'no-reply@juniorbay.com'),
        'from_name': get_config_value('ses_from_name', 'JuniorBay'),
        'configuration_set': get_config_value('ses_configuration_set', ''),
        'reply_to_default': get_config_value('ses_reply_to_default', 'support@juniorbay.com')
    }

def get_cognito_config() -> Dict[str, Any]:
    """
    Get Cognito configuration for current environment.
    
    Returns:
        Dictionary with Cognito settings
    """
    return {
        'region': get_config_value('cognito_region', 'us-west-2'),
        'user_pool_id': get_config_value('cognito_user_pool_id', ''),
        'client_id': get_config_value('cognito_client_id', ''),
    }

def invalidate_cache():
    """Force cache invalidation."""
    global _config_cache, _cache_timestamp
    _config_cache = {}
    _cache_timestamp = None
    logger.info("Config cache invalidated")
    

def get_offers_base_url(client_id: str, mode: str = "test") -> str:
    """Get offer base URL from config."""
    environment = "prod" if mode == "live" else "dev"
    base_url = get_config_value("offers_base_url", environment=environment)
    
    if not base_url:
        base_url = "https://juniorbay.com/tenants/"
    
    if not base_url.endswith("/"):
        base_url += "/"
    
    return f"{base_url}{client_id}/"