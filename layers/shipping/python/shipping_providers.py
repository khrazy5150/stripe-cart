import os
import json
import logging
import requests
from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod

# shipping_providers.py - Multi-provider shipping integration

logger = logging.getLogger()
logger.setLevel(logging.INFO)

class ShippingProvider(ABC):
    """Base class for shipping providers."""
    
    def __init__(self, api_key: str, test_mode: bool = True):
        self.api_key = api_key
        self.test_mode = test_mode
    
    @abstractmethod
    def create_shipment(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a shipment and return tracking info."""
        pass
    
    @abstractmethod
    def get_rates(self, from_address: Dict, to_address: Dict, 
                  parcel: Dict) -> List[Dict[str, Any]]:
        """Get shipping rates."""
        pass
    
    @abstractmethod
    def get_tracking(self, tracking_number: str) -> Dict[str, Any]:
        """Get tracking information."""
        pass


class ShippoProvider(ShippingProvider):
    """Shippo integration."""
    
    BASE_URL = "https://api.goshippo.com"
    
    def __init__(self, api_key: str, test_mode: bool = True):
        super().__init__(api_key, test_mode)
        self.headers = {
            "Authorization": f"ShippoToken {api_key}",
            "Content-Type": "application/json"
        }

    def purchase_rate(self, rate_id: str) -> Dict[str, Any]:
        """Purchase a specific Shippo rate by its object_id."""
        try:
            transaction_data = {
                "rate": rate_id,
                "label_file_type": "PDF",
                "async": False
            }
            
            response = requests.post(
                f"{self.BASE_URL}/transactions/",
                headers=self.headers,
                json=transaction_data
            )
            response.raise_for_status()
            transaction = response.json()
            
            # Get rate details for the response - handle both object and string
            rate_info = transaction.get('rate', {})
            if isinstance(rate_info, str):
                # If rate is just an ID string, we won't have detailed info
                rate_info = {}
            
            # Handle servicelevel which might also be a string or dict
            service_level = rate_info.get('servicelevel', {})
            if isinstance(service_level, str):
                service_name = service_level
            elif isinstance(service_level, dict):
                service_name = service_level.get('name', '')
            else:
                service_name = ''
            
            return {
                "success": True,
                "tracking_number": transaction.get('tracking_number'),
                "tracking_url": transaction.get('tracking_url_provider'),
                "label_url": transaction.get('label_url'),
                "carrier": rate_info.get('provider') if isinstance(rate_info, dict) else transaction.get('carrier'),
                "service": service_name,
                "cost": rate_info.get('amount') if isinstance(rate_info, dict) else transaction.get('amount'),
                "currency": rate_info.get('currency') if isinstance(rate_info, dict) else transaction.get('currency'),
                "raw_response": transaction
            }
        except Exception as e:
            logger.error(f"Shippo purchase_rate error: {str(e)}")
            return {"success": False, "error": str(e)}
        
    
    def create_shipment(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create Shippo shipment."""
        try:
            # Create address objects
            from_addr = self._create_address(order_data['from_address'])
            to_addr = self._create_address(order_data['to_address'])
            parcel = self._create_parcel(order_data['parcel'])
            
            # Create shipment
            shipment_data = {
                "address_from": from_addr['object_id'],
                "address_to": to_addr['object_id'],
                "parcels": [parcel['object_id']],
                "async": False
            }
            
            response = requests.post(
                f"{self.BASE_URL}/shipments/",
                headers=self.headers,
                json=shipment_data
            )
            response.raise_for_status()
            shipment = response.json()
            
            # Get cheapest rate
            rates = shipment.get('rates', [])
            if not rates:
                raise ValueError("No shipping rates available")
            
            cheapest_rate = min(rates, key=lambda r: float(r['amount']))
            
            # Purchase label
            transaction_data = {
                "rate": cheapest_rate['object_id'],
                "label_file_type": "PDF",
                "async": False
            }
            
            response = requests.post(
                f"{self.BASE_URL}/transactions/",
                headers=self.headers,
                json=transaction_data
            )
            response.raise_for_status()
            transaction = response.json()
            
            return {
                "success": True,
                "tracking_number": transaction.get('tracking_number'),
                "tracking_url": transaction.get('tracking_url_provider'),
                "label_url": transaction.get('label_url'),
                "carrier": cheapest_rate.get('provider'),
                "service": cheapest_rate.get('servicelevel', {}).get('name'),
                "cost": cheapest_rate.get('amount'),
                "currency": cheapest_rate.get('currency'),
                "raw_response": transaction
            }
            
        except Exception as e:
            logger.error(f"Shippo create_shipment error: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def _create_address(self, address: Dict) -> Dict:
        """Create Shippo address object."""
        response = requests.post(
            f"{self.BASE_URL}/addresses/",
            headers=self.headers,
            json={
                "name": address.get('name', ''),
                "street1": address.get('street1') or address.get('line1', ''),
                "street2": address.get('street2') or address.get('line2', ''),
                "city": address.get('city', ''),
                "state": address.get('state', ''),
                "zip": address.get('zip') or address.get('postal_code', ''),
                "country": address.get('country', 'US'),
                "phone": address.get('phone', ''),
                "email": address.get('email', '')
            }
        )
        response.raise_for_status()
        return response.json()
    
    def _create_parcel(self, parcel: Dict) -> Dict:
        """Create Shippo parcel object."""
        response = requests.post(
            f"{self.BASE_URL}/parcels/",
            headers=self.headers,
            json={
                "length": str(parcel.get('length', '10')),
                "width": str(parcel.get('width', '8')),
                "height": str(parcel.get('height', '4')),
                "distance_unit": parcel.get('distance_unit', 'in'),
                "weight": str(parcel.get('weight', '1')),
                "mass_unit": parcel.get('mass_unit', 'lb')
            }
        )
        response.raise_for_status()
        return response.json()
    
    def get_rates(self, from_address: Dict, to_address: Dict, 
                  parcel: Dict) -> List[Dict[str, Any]]:
        """Get Shippo rates."""
        try:
            from_addr = self._create_address(from_address)
            to_addr = self._create_address(to_address)
            parcel_obj = self._create_parcel(parcel)
            
            shipment_data = {
                "address_from": from_addr['object_id'],
                "address_to": to_addr['object_id'],
                "parcels": [parcel_obj['object_id']],
                "async": False
            }
            
            response = requests.post(
                f"{self.BASE_URL}/shipments/",
                headers=self.headers,
                json=shipment_data
            )
            response.raise_for_status()
            shipment = response.json()
            
            return [
                {
                    "carrier": rate.get('provider'),
                    "service": rate.get('servicelevel', {}).get('name'),
                    "rate": float(rate.get('amount', 0)),
                    "currency": rate.get('currency'),
                    "delivery_days": rate.get('estimated_days')
                }
                for rate in shipment.get('rates', [])
            ]
        except Exception as e:
            logger.error(f"Shippo get_rates error: {str(e)}")
            return []
    
    def get_tracking(self, tracking_number: str) -> Dict[str, Any]:
        """Get Shippo tracking."""
        try:
            response = requests.get(
                f"{self.BASE_URL}/tracks/{tracking_number}",
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Shippo tracking error: {str(e)}")
            return {"error": str(e)}
    
    def get_rates_with_shipment(self, from_address: Dict, to_address: Dict, 
                           parcel: Dict) -> Dict[str, Any]:
        """Get Shippo rates along with shipment ID for later purchase."""
        try:
            from_addr = self._create_address(from_address)
            to_addr = self._create_address(to_address)
            parcel_obj = self._create_parcel(parcel)
            
            shipment_data = {
                "address_from": from_addr['object_id'],
                "address_to": to_addr['object_id'],
                "parcels": [parcel_obj['object_id']],
                "async": False
            }
            
            response = requests.post(
                f"{self.BASE_URL}/shipments/",
                headers=self.headers,
                json=shipment_data
            )
            response.raise_for_status()
            shipment = response.json()
            
            # Log the full shipment response to see messages/errors
            logger.info(f"Shippo shipment status: {shipment.get('status')}")
            
            # Check for carrier-specific messages
            if 'messages' in shipment and shipment['messages']:
                logger.warning(f"Shippo messages: {shipment['messages']}")
            
            # Log all available rates
            all_rates = shipment.get('rates', [])
            logger.info(f"Shippo returned {len(all_rates)} total rates")
            
            # Group by carrier for easier debugging
            carriers = {}
            for rate in all_rates:
                carrier = rate.get('provider')
                if carrier not in carriers:
                    carriers[carrier] = []
                carriers[carrier].append(rate.get('servicelevel', {}).get('name'))
                logger.info(f"Rate: {carrier} - {rate.get('servicelevel', {}).get('name')} - ${rate.get('amount')}")
            
            logger.info(f"Carriers represented: {list(carriers.keys())}")
            
            rates = [
                {
                    "rate_id": rate['object_id'],
                    "carrier": rate.get('provider'),
                    "service": rate.get('servicelevel', {}).get('name'),
                    "rate": float(rate.get('amount', 0)),
                    "currency": rate.get('currency'),
                    "delivery_days": rate.get('estimated_days')
                }
                for rate in all_rates
            ]
            
            return {
                "shipment_id": shipment.get('object_id'),
                "rates": rates
            }
        except Exception as e:
            logger.error(f"Shippo get_rates_with_shipment error: {str(e)}")
            return {"rates": []}


class EasyPostProvider(ShippingProvider):
    """EasyPost integration."""
    
    BASE_URL = "https://api.easypost.com/v2"
    
    def __init__(self, api_key: str, test_mode: bool = True):
        super().__init__(api_key, test_mode)
        self.auth = (api_key, '')  # EasyPost uses HTTP Basic Auth
    
    def create_shipment(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create EasyPost shipment."""
        try:
            # Create shipment
            shipment_data = {
                "shipment": {
                    "to_address": order_data['to_address'],
                    "from_address": order_data['from_address'],
                    "parcel": order_data['parcel']
                }
            }
            
            response = requests.post(
                f"{self.BASE_URL}/shipments",
                auth=self.auth,
                json=shipment_data
            )
            response.raise_for_status()
            shipment = response.json()
            
            # Get cheapest rate
            rates = shipment.get('rates', [])
            if not rates:
                raise ValueError("No rates available")
            
            cheapest = min(rates, key=lambda r: float(r['rate']))
            
            # Buy shipment
            buy_data = {"rate": {"id": cheapest['id']}}
            response = requests.post(
                f"{self.BASE_URL}/shipments/{shipment['id']}/buy",
                auth=self.auth,
                json=buy_data
            )
            response.raise_for_status()
            result = response.json()
            
            postage = result.get('postage_label', {})
            
            return {
                "success": True,
                "tracking_number": result.get('tracking_code'),
                "tracking_url": result.get('tracker', {}).get('public_url'),
                "label_url": postage.get('label_url'),
                "carrier": cheapest.get('carrier'),
                "service": cheapest.get('service'),
                "cost": cheapest.get('rate'),
                "currency": cheapest.get('currency'),
                "raw_response": result
            }
            
        except Exception as e:
            logger.error(f"EasyPost error: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def get_rates(self, from_address: Dict, to_address: Dict, 
                  parcel: Dict) -> List[Dict[str, Any]]:
        """Get EasyPost rates."""
        try:
            data = {
                "shipment": {
                    "to_address": to_address,
                    "from_address": from_address,
                    "parcel": parcel
                }
            }
            
            response = requests.post(
                f"{self.BASE_URL}/shipments",
                auth=self.auth,
                json=data
            )
            response.raise_for_status()
            shipment = response.json()
            
            return [
                {
                    "carrier": rate.get('carrier'),
                    "service": rate.get('service'),
                    "rate": float(rate.get('rate', 0)),
                    "currency": rate.get('currency'),
                    "delivery_days": rate.get('delivery_days')
                }
                for rate in shipment.get('rates', [])
            ]
        except Exception as e:
            logger.error(f"EasyPost rates error: {str(e)}")
            return []
    
    def get_tracking(self, tracking_number: str) -> Dict[str, Any]:
        """Get EasyPost tracking."""
        try:
            response = requests.get(
                f"{self.BASE_URL}/trackers/{tracking_number}",
                auth=self.auth
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {"error": str(e)}


class ShipStationProvider(ShippingProvider):
    """ShipStation integration."""
    
    BASE_URL = "https://ssapi.shipstation.com"
    
    def __init__(self, api_key: str, test_mode: bool = True, api_secret: str = ""):
        super().__init__(api_key, test_mode)
        self.api_secret = api_secret
        self.auth = (api_key, api_secret)
    
    def create_shipment(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create ShipStation label."""
        try:
            label_data = {
                "carrierCode": order_data.get('carrier', 'stamps_com'),
                "serviceCode": order_data.get('service', 'usps_priority_mail'),
                "packageCode": "package",
                "confirmation": "none",
                "shipDate": order_data.get('ship_date'),
                "weight": {
                    "value": order_data['parcel'].get('weight', 1),
                    "units": "pounds"
                },
                "dimensions": {
                    "length": order_data['parcel'].get('length', 10),
                    "width": order_data['parcel'].get('width', 8),
                    "height": order_data['parcel'].get('height', 4),
                    "units": "inches"
                },
                "shipFrom": self._format_address(order_data['from_address']),
                "shipTo": self._format_address(order_data['to_address']),
                "testLabel": self.test_mode
            }
            
            response = requests.post(
                f"{self.BASE_URL}/shipments/createlabel",
                auth=self.auth,
                json=label_data
            )
            response.raise_for_status()
            result = response.json()
            
            return {
                "success": True,
                "tracking_number": result.get('trackingNumber'),
                "tracking_url": f"https://tools.usps.com/go/TrackConfirmAction?tLabels={result.get('trackingNumber')}",
                "label_url": result.get('labelData'),  # Base64 encoded
                "carrier": result.get('carrierCode'),
                "service": result.get('serviceCode'),
                "cost": result.get('shipmentCost'),
                "currency": "USD",
                "raw_response": result
            }
            
        except Exception as e:
            logger.error(f"ShipStation error: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def _format_address(self, address: Dict) -> Dict:
        """Format address for ShipStation."""
        return {
            "name": address.get('name', ''),
            "street1": address.get('street1') or address.get('line1', ''),
            "street2": address.get('street2') or address.get('line2', ''),
            "city": address.get('city', ''),
            "state": address.get('state', ''),
            "postalCode": address.get('zip') or address.get('postal_code', ''),
            "country": address.get('country', 'US'),
            "phone": address.get('phone', '')
        }
    
    def get_rates(self, from_address: Dict, to_address: Dict, 
                  parcel: Dict) -> List[Dict[str, Any]]:
        """ShipStation doesn't have a dedicated rates endpoint."""
        return []
    
    def get_tracking(self, tracking_number: str) -> Dict[str, Any]:
        """Get ShipStation tracking - requires order lookup."""
        return {"error": "Use carrier tracking URL instead"}


class EasyShipProvider(ShippingProvider):
    """EasyShip integration."""
    
    BASE_URL = "https://api.easyship.com/2023-01"
    
    def __init__(self, api_key: str, test_mode: bool = True):
        super().__init__(api_key, test_mode)
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def create_shipment(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create EasyShip shipment."""
        try:
            # Create shipment
            shipment_data = {
                "platform_name": "Custom",
                "platform_order_number": order_data.get('order_id'),
                "destination_country_alpha2": order_data['to_address'].get('country', 'US'),
                "destination_city": order_data['to_address'].get('city'),
                "destination_postal_code": order_data['to_address'].get('zip') or order_data['to_address'].get('postal_code'),
                "destination_state": order_data['to_address'].get('state'),
                "destination_line_1": order_data['to_address'].get('street1') or order_data['to_address'].get('line1'),
                "destination_line_2": order_data['to_address'].get('street2') or order_data['to_address'].get('line2'),
                "destination_name": order_data['to_address'].get('name'),
                "destination_phone_number": order_data['to_address'].get('phone'),
                "items": [{
                    "description": "Product",
                    "sku": order_data.get('product_id', 'SKU001'),
                    "quantity": 1,
                    "dimensions": {
                        "length": order_data['parcel'].get('length', 10),
                        "width": order_data['parcel'].get('width', 8),
                        "height": order_data['parcel'].get('height', 4)
                    },
                    "actual_weight": order_data['parcel'].get('weight', 1)
                }]
            }
            
            response = requests.post(
                f"{self.BASE_URL}/shipments",
                headers=self.headers,
                json=shipment_data
            )
            response.raise_for_status()
            result = response.json()
            
            return {
                "success": True,
                "shipment_id": result.get('shipment', {}).get('easyship_shipment_id'),
                "tracking_number": result.get('shipment', {}).get('tracking_number'),
                "tracking_url": result.get('shipment', {}).get('tracking_page_url'),
                "label_url": result.get('shipment', {}).get('label_url'),
                "raw_response": result
            }
            
        except Exception as e:
            logger.error(f"EasyShip error: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def get_rates(self, from_address: Dict, to_address: Dict, 
                  parcel: Dict) -> List[Dict[str, Any]]:
        """Get EasyShip rates."""
        try:
            data = {
                "origin_country_alpha2": from_address.get('country', 'US'),
                "origin_postal_code": from_address.get('zip') or from_address.get('postal_code'),
                "destination_country_alpha2": to_address.get('country', 'US'),
                "destination_postal_code": to_address.get('zip') or to_address.get('postal_code'),
                "items": [{
                    "quantity": 1,
                    "dimensions": {
                        "length": parcel.get('length', 10),
                        "width": parcel.get('width', 8),
                        "height": parcel.get('height', 4)
                    },
                    "actual_weight": parcel.get('weight', 1)
                }]
            }
            
            response = requests.post(
                f"{self.BASE_URL}/rates",
                headers=self.headers,
                json=data
            )
            response.raise_for_status()
            result = response.json()
            
            return [
                {
                    "carrier": rate.get('courier_name'),
                    "service": rate.get('full_description'),
                    "rate": float(rate.get('total_charge', 0)),
                    "currency": rate.get('currency'),
                    "delivery_days": rate.get('min_delivery_time')
                }
                for rate in result.get('rates', [])
            ]
        except Exception as e:
            logger.error(f"EasyShip rates error: {str(e)}")
            return []
    
    def get_tracking(self, tracking_number: str) -> Dict[str, Any]:
        """Get EasyShip tracking."""
        try:
            response = requests.get(
                f"{self.BASE_URL}/tracking/{tracking_number}",
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {"error": str(e)}

# Factory function
def get_shipping_provider(provider_name: str, config: Dict[str, Any]) -> Optional[ShippingProvider]:
    """Get shipping provider instance."""
    api_key = config.get('api_key', '')
    test_mode = config.get('test_mode', True)
    
    providers = {
        'shippo': lambda: ShippoProvider(api_key, test_mode),
        'easypost': lambda: EasyPostProvider(api_key, test_mode),
        'shipstation': lambda: ShipStationProvider(
            api_key, 
            test_mode,
            config.get('api_secret', '')
        ),
        'easyship': lambda: EasyShipProvider(api_key, test_mode)
    }
    
    provider_func = providers.get(provider_name.lower())
    if not provider_func:
        logger.error(f"Unknown provider: {provider_name}")
        return None
    
    try:
        return provider_func()
    except Exception as e:
        logger.error(f"Failed to initialize {provider_name}: {str(e)}")
        return None