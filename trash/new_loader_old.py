import os
import boto3
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger()
dynamodb = boto3.resource("dynamodb")

# Cache config to avoid repeated DynamoDB calls
_config_cache: Dict[str, Any] = {}
_cache_initialized = False

def load_config(force_reload: bool = False) -> Dict[str, Any]:
    """Load all configuration from app-config table."""
    global _config_cache, _cache_initialized
    
    if _cache_initialized and not force_reload:
        return _config_cache
    
    try:
        config_table_name = os.environ.get("APP_CONFIG_TABLE", "app-config")
        config_table = dynamodb.Table(config_table_name)
        
        # Get current environment
        environment = os.environ.get("ENVIRONMENT", "dev")
        
        # Scan for all config for this environment
        response = config_table.query(
            IndexName="environment-index",  # Assumes GSI on environment
            KeyConditionExpression="environment = :env",
            ExpressionAttributeValues={":env": environment}
        )
        
        # Build config dict
        config = {"environment": environment}
        for item in response.get("Items", []):
            config[item["config_key"]] = item.get("value")
        
        _config_cache = config
        _cache_initialized = True
        
        logger.info(f"Loaded config for environment: {environment}")
        return config
        
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return {"environment": "dev"}

def get_config_value(key: str, default: Any = None, environment: Optional[str] = None) -> Any:
    """Get a specific config value."""
    try:
        if not environment:
            environment = os.environ.get("ENVIRONMENT", "dev")
        
        config_table_name = os.environ.get("APP_CONFIG_TABLE", "app-config")
        config_table = dynamodb.Table(config_table_name)
        
        response = config_table.get_item(
            Key={"config_key": key, "environment": environment}
        )
        
        if "Item" in response:
            return response["Item"].get("value", default)
        
        return default
        
    except Exception as e:
        logger.warning(f"Failed to get config {key}: {e}")
        return default

def get_base_frontend_url(client_id: str, mode: str = "test") -> str:
    """
    Construct base frontend URL from system config.
    
    Args:
        client_id: The tenant client ID
        mode: 'test' or 'live' (maps to dev/test or prod)
    
    Returns:
        Full base URL: {base_url}/{client_id}/
    """
    # Map Stripe mode to environment
    environment = "prod" if mode == "live" else "test"
    
    # Get base URL from config
    base_url = get_config_value("base_frontend_url", environment=environment)
    
    # Fallback to dev if not found
    if not base_url:
        base_url = get_config_value("base_frontend_url", environment="dev")
    
    # Final fallback
    if not base_url:
        base_url = "https://juniorbay.com/tenants/"
    
    # Ensure trailing slash
    if not base_url.endswith("/"):
        base_url += "/"
    
    return f"{base_url}{client_id}/"

def get_s3_bucket(mode: str = "test") -> str:
    """Get S3 bucket for given mode."""
    environment = "prod" if mode == "live" else "test"
    return get_config_value("s3_bucket", environment=environment, default="juniorbay-offers")