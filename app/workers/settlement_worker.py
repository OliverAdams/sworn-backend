# workers/settlement_worker.py
import json
import uuid
from datetime import datetime
from typing import Optional
import logging

from app.workers.celery_app import app
from app.workers.shared_worker_utils import get_seasonal_modifiers
from database.connection import SessionLocal, get_db

from app.models.core import (
    Settlements,
    SettlementResources,
    ResourceSites,
    ResourceSiteTypes,
    ResourceSiteStages,
    ResourceTypes,
    Worlds
)

# Import the service that contains the class-based implementation
from app.game_state.services.settlement_service import SettlementService

logger = logging.getLogger(__name__)

@app.task
def process_settlement_production(settlement_id):
    """
    Process resource production for a settlement.
    
    This is the legacy implementation. For new code, use process_settlement_growth instead.
    
    Args:
        settlement_id: The ID of the settlement to process
        
    Returns:
        dict: Result of the settlement production processing
    """
    db = SessionLocal()
    try:
        # Try the new class-based implementation first
        try:
            service = SettlementService(db)
            result = service.process_settlement_growth(settlement_id)
            if result["status"] == "success":
                logger.info(f"Processed production for settlement {settlement_id} using new implementation")
                return {"status": "success", "settlement_id": settlement_id}
            else:
                # Fall back to legacy implementation
                logger.warning(f"New implementation failed, falling back to legacy: {result.get('message')}")
        except Exception as e:
            logger.warning(f"Error in new implementation, falling back to legacy: {e}")
            
        # Legacy implementation
        # 1. Process resources from resource sites
        process_resource_sites(db, settlement_id)
        
        # 2. Process resources from buildings (to be implemented)
        
        # 3. Process resource consumption (to be implemented)
        
        logger.info(f"Processed production for settlement {settlement_id} using legacy implementation")
        return {"status": "success", "settlement_id": settlement_id}
    except Exception as e:
        logger.error(f"Error processing settlement {settlement_id}: {str(e)}")
        return {"status": "error", "settlement_id": settlement_id, "error": str(e)}
    finally:
        db.close()

def process_resource_sites(db, settlement_id):
    """Process resource production from all operational resource sites in a settlement"""
    
    # Get the settlement to find its world
    settlement = db.query(Settlements).filter(
        Settlements.settlement_id == str(settlement_id)
    ).first()
    
    if not settlement:
        logger.error(f"Settlement {settlement_id} not found")
        return
    
    # Get seasonal modifiers for the world
    season_data = get_seasonal_modifiers(str(settlement.world_id))
    seasonal_modifiers = season_data.get("modifiers", {})
    current_season = season_data.get("season", "spring")
    
    logger.info(f"Processing settlement {settlement.settlement_name} production with {current_season} modifiers: {seasonal_modifiers}")
    
    # Get all operational sites for this settlement
    sites = db.query(ResourceSites).filter(
        ResourceSites.settlement_id == str(settlement_id),
        ResourceSites.current_stage != "undiscovered",
        ResourceSites.current_stage != "depleted"
    ).all()
    
    if not sites:
        logger.info(f"No productive resource sites found for settlement {settlement_id}")
        return
    
    # Process each resource site
    timestamp = datetime.now()
    for site in sites:
        # Get the site type information
        site_type = db.query(ResourceSiteTypes).filter(
            ResourceSiteTypes.site_type_id == site.site_type_id
        ).first()
        
        if not site_type:
            logger.warning(f"Site type not found for site {site.site_id}")
            continue
        
        # Hardcoded production rates for different site stages
        site_stage_production = {
            # Gold Vein stages
            "gold_mine": {"gold": 3, "stone": 2},
            
            # Iron Vein stages
            "small_mine": {"iron": 5, "stone": 2},
            "established_mine": {"iron": 12, "stone": 3},
            
            # Stone Quarry stages
            "small_quarry": {"stone": 10},
            "quarry": {"stone": 25},
            
            # Forest Grove stages
            "small_lumber_camp": {"wood": 10, "herbs": 2},
            "lumber_camp": {"wood": 20, "herbs": 3},
            
            # Herb Grove stages
            "herb_garden": {"herbs": 12, "food": 4},
            
            # Fertile Soil stages
            "small_farm": {"food": 12},
            "established_farm": {"food": 20},
            
            # Default for discovered sites
            "discovered": {"food": 3, "wood": 3, "herbs": 2}
        }
        
        # Get production rates for this stage
        production_rates = site_stage_production.get(site.current_stage)
        if not production_rates:
            # Default to minimal production for discovered sites
            if site.current_stage == "discovered":
                production_rates = site_stage_production["discovered"]
            else:
                logger.warning(f"No production data for stage {site.current_stage} of site {site.site_id}")
                continue
            
        # Map to use actual resource IDs in your database
        resource_id_map = {
            "iron": "6e7e41a9-c3f6-4723-b510-50bd9f537b8a",  # Iron Ore
            "gold": "aa09429d-4df0-4834-a503-b2653e5a52bd",  # Gold
            "stone": "ba009e21-4bbd-4998-ad15-e7cb32a19636",  # Stone
            "wood": "c4aa2349-409f-4107-ac8a-71331e5f9e92",   # Logs
            "herbs": "51c21030-d6f4-42c4-b63f-343d11a818f5",  # Herbs
            "fish": "ad4b90c1-2e0d-4c1e-9beb-84cfc18f8f5b",   # Fish
            "food": "7bf0d22e-cdef-4ecc-aff6-33ae9c47f21e",   # Meat
            "water": "fc5a66f8-4faa-43ad-a20f-9e386afab6b1",  # Water
        }
        
        # Apply the site's production multiplier and development level
        multiplier = site.production_multiplier if site.production_multiplier else 1.0
        development = site.development_level if site.development_level is not None else 0.0
        
        # Increase production based on development level (0.0 to 1.0)
        dev_bonus = 1.0 + (development * 0.5)  # Up to 50% bonus at full development
        
        # Update resources in the settlement's inventory
        for resource_code, amount in production_rates.items():
            # Apply seasonal modifier for this resource type
            # Get the season modifier for this resource (default to 1.0 if not defined)
            season_modifier = seasonal_modifiers.get(resource_code, 1.0)
            
            # Calculate actual production with all modifiers
            produced_amount = int(amount * multiplier * dev_bonus * season_modifier)
            
            # Log detailed production calculation
            logger.debug(f"Resource calculation for {resource_code}: {amount} * {multiplier} (site) * {dev_bonus} (dev) * {season_modifier} (season) = {produced_amount}")
            
            # Get the resource type ID using our mapping
            resource_type_id = resource_id_map.get(resource_code)
            
            if not resource_type_id:
                logger.warning(f"Resource type '{resource_code}' not found in resource_id_map")
                continue
            
            # Check if the settlement already has this resource
            settlement_resource = db.query(SettlementResources).filter(
                SettlementResources.settlement_id == str(settlement_id),
                SettlementResources.resource_type_id == resource_type_id
            ).first()
            
            if settlement_resource:
                # Update existing resource
                settlement_resource.quantity += produced_amount
                settlement_resource.last_updated = timestamp
                logger.info(f"Added {produced_amount} {resource_code} to settlement {settlement_id} from {site_type.site_name}")
            else:
                # Create new resource entry
                new_resource = SettlementResources(
                    settlement_resource_id=str(uuid.uuid4()),
                    settlement_id=str(settlement_id),
                    resource_type_id=resource_type_id,
                    quantity=produced_amount,
                    last_updated=timestamp
                )
                db.add(new_resource)
                logger.info(f"Created new resource entry with {produced_amount} {resource_code} for settlement {settlement_id}")
        
        # Update the resource site - increase depletion slightly for non-renewable resources
        if site_type.site_category == "mining":
            # Mining sites deplete over time
            depletion_rate = 0.01  # 1% depletion per cycle
            if site.depletion_level is not None:
                site.depletion_level += depletion_rate
                
                # Check if site has become depleted
                if site.depletion_level >= 1.0:
                    site.depletion_level = 1.0
                    site.current_stage = "depleted"
                    logger.info(f"Resource site {site.site_id} ({site_type.site_name}) has become depleted")
        
        # Update last_updated timestamp
        site.last_updated = timestamp
    
    # Commit all changes
    db.commit()


# New settlement worker functions using the class-based architecture

@app.task
def process_settlement_growth(settlement_id: str):
    """
    Process the growth and production of a settlement.
    
    This task delegates to the SettlementService, which contains the actual implementation
    using the new class-based game state architecture.
    
    Args:
        settlement_id (str): The ID of the settlement to process
        
    Returns:
        dict: Result of the settlement processing
    """
    logger.info(f"Processing growth for settlement {settlement_id}")
    
    # Create database session
    db = SessionLocal()
    try:
        # Create service with the database session
        service = SettlementService(db)
        
        # Delegate to service implementation
        result = service.process_settlement_growth(settlement_id)
        
        # Log the result
        if result["status"] == "success":
            logger.info(f"Successfully processed settlement {settlement_id}")
            logger.debug(f"Settlement processing details: {result}")
        else:
            logger.warning(f"Failed to process settlement {settlement_id}: {result.get('message', 'Unknown error')}")
        
        return result
    except Exception as e:
        logger.exception(f"Error in process_settlement_growth task: {e}")
        return {"status": "error", "message": f"Task error: {str(e)}"}
    finally:
        db.close()

@app.task
def create_new_settlement(name: str, location_id: str, world_id: str):
    """
    Create a new settlement at the specified location.
    
    Args:
        name (str): The name of the settlement
        location_id (str): The ID of the location (area)
        world_id (str): The ID of the world
        
    Returns:
        dict: Result of settlement creation
    """
    logger.info(f"Creating new settlement {name} at location {location_id}")
    
    # Create database session
    db = SessionLocal()
    try:
        # Create service with the database session
        service = SettlementService(db)
        
        # Delegate to service implementation
        result = service.create_settlement(name, location_id, world_id)
        
        # Log the result
        if result["status"] == "success":
            logger.info(f"Successfully created settlement {name} (ID: {result.get('settlement_id')})")
        else:
            logger.warning(f"Failed to create settlement {name}: {result.get('message', 'Unknown error')}")
        
        return result
    except Exception as e:
        logger.exception(f"Error in create_new_settlement task: {e}")
        return {"status": "error", "message": f"Task error: {str(e)}"}
    finally:
        db.close()

@app.task
def start_building_construction(settlement_id: str, building_type: str):
    """
    Start construction of a new building in a settlement.
    
    Args:
        settlement_id (str): The settlement ID
        building_type (str): The type of building to construct
        
    Returns:
        dict: Result of construction initiation
    """
    logger.info(f"Starting construction of {building_type} in settlement {settlement_id}")
    
    # Create database session
    db = SessionLocal()
    try:
        # Create service with the database session
        service = SettlementService(db)
        
        # Delegate to service implementation
        result = service.start_building_construction(settlement_id, building_type)
        
        # Log the result
        if result["status"] == "success":
            logger.info(f"Successfully started construction of {building_type} in settlement {settlement_id}")
        else:
            logger.warning(f"Failed to start construction in settlement {settlement_id}: {result.get('message', 'Unknown error')}")
        
        return result
    except Exception as e:
        logger.exception(f"Error in start_building_construction task: {e}")
        return {"status": "error", "message": f"Task error: {str(e)}"}
    finally:
        db.close()

@app.task
def start_building_repair(settlement_id: str, building_id: str):
    """
    Start repair of a damaged building.
    
    Args:
        settlement_id (str): The settlement ID
        building_id (str): The building ID
        
    Returns:
        dict: Result of repair initiation
    """
    logger.info(f"Starting repair of building {building_id} in settlement {settlement_id}")
    
    # Create database session
    db = SessionLocal()
    try:
        # Create service with the database session
        service = SettlementService(db)
        
        # Delegate to service implementation
        result = service.start_building_repair(settlement_id, building_id)
        
        # Log the result
        if result["status"] == "success":
            logger.info(f"Successfully started repair of building {building_id} in settlement {settlement_id}")
        else:
            logger.warning(f"Failed to start repair in settlement {settlement_id}: {result.get('message', 'Unknown error')}")
        
        return result
    except Exception as e:
        logger.exception(f"Error in start_building_repair task: {e}")
        return {"status": "error", "message": f"Task error: {str(e)}"}
    finally:
        db.close()

@app.task
def process_all_settlements(world_id: Optional[str] = None):
    """
    Process all settlements in a world (or all worlds if none specified).
    
    This task delegates to the SettlementService, which contains the actual implementation
    using the new class-based game state architecture.
    
    Args:
        world_id (str, optional): The world ID to process settlements for, or None for all worlds
        
    Returns:
        dict: Result of processing all settlements
    """
    logger.info(f"Processing all settlements" + (f" in world {world_id}" if world_id else ""))
    
    # Create database session
    db = SessionLocal()
    try:
        # Create service with the database session
        service = SettlementService(db)
        
        # Since we need to call an async method from a sync context, we'll use a helper
        import asyncio
        
        # Create a new event loop for this task
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run the async method and get its result
        result = loop.run_until_complete(service.process_all_settlements(world_id))
        loop.close()
        
        # Log the result
        if result["status"] == "success":
            logger.info(f"Successfully processed {result.get('processed', 0)}/{result.get('total', 0)} settlements")
        else:
            logger.warning(f"Failed to process settlements: {result.get('message', 'Unknown error')}")
        
        return result
    except Exception as e:
        logger.exception(f"Error in process_all_settlements task: {e}")
        return {"status": "error", "message": f"Task error: {str(e)}"}
    finally:
        db.close()