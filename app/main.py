#!/usr/bin/env python3
"""
InkyPi Wingfoil Forecast Service
================================

A comprehensive weather and wingfoil condition service for InkyPi displays.
Fetches marine weather data, evaluates wingfoil conditions, and provides
API endpoints for InkyPi integration.

Author: InkyPi Community
License: GPL-3.0 (same as InkyPi project)
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Tuple
import requests
from flask import Flask, jsonify, render_template, request, send_from_directory
from dataclasses import dataclass, asdict
import asyncio
import aiohttp
from concurrent.futures import ThreadPoolExecutor
from dateutil import parser as dateparser

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Limit upload payloads (defense-in-depth)
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024  # 8MB
@app.route('/api/spot-map', methods=['POST'])
def upload_spot_map():
    try:
        # Optional admin token enforcement for mutating endpoint
        if not _require_admin(request):
            return jsonify({"error": "Unauthorized"}), 401
        if 'file' not in request.files:
            return jsonify({"error": "No file part"}), 400
        f = request.files['file']
        if not f or f.filename == '':
            return jsonify({"error": "No selected file"}), 400
        # Simple extension/type allowlist
        filename_l = f.filename.lower()
        if not (filename_l.endswith('.jpg') or filename_l.endswith('.jpeg') or filename_l.endswith('.png')):
            return jsonify({"error": "Only JPG/PNG allowed"}), 400
        # Save into Flask's static folder so /static/spot-map.jpg serves correctly
        static_dir = app.static_folder or os.path.join(os.path.dirname(__file__), 'static')
        os.makedirs(static_dir, exist_ok=True)
        path = os.path.join(static_dir, 'spot-map.jpg')
        f.save(path)
        return jsonify({"message": "Map uploaded", "path": "/static/spot-map.jpg"})
    except Exception as e:
        logger.error(f"Error uploading spot map: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/spot-map')
def serve_spot_map():
    try:
        import os
        # Prefer Flask static folder
        static_dir = app.static_folder or os.path.join(os.path.dirname(__file__), 'static')
        primary = os.path.join(static_dir, 'spot-map.jpg')
        if os.path.exists(primary):
            return send_from_directory(static_dir, 'spot-map.jpg')
        # Fallback to legacy location from early versions
        legacy_dir = '/app/static'
        legacy = os.path.join(legacy_dir, 'spot-map.jpg')
        if os.path.exists(legacy):
            return send_from_directory(legacy_dir, 'spot-map.jpg')
        return jsonify({"error": "map not found"}), 404
    except Exception as e:
        logger.error(f"Error serving spot map: {e}")
        return jsonify({"error": str(e)}), 500

@dataclass
class WeatherConditions:
    """Data class for weather conditions"""
    timestamp: str
    location: str
    latitude: float
    longitude: float
    wind_speed_ms: float
    wind_speed_knots: float
    wind_direction: int
    wind_gust_ms: float
    temperature: float
    water_temperature: float
    wave_height: float
    wave_period: float
    wave_direction: int
    pressure: float
    humidity: int
    visibility: float
    uv_index: float
    # Derived sport metrics (optional)
    shore_angle_deg: int = 0
    chop_index: float = 0.0
    
@dataclass
class WingfoilConditions:
    """Data class for wingfoil evaluation"""
    suitable: bool
    score: int  # 0-100
    wind_evaluation: str
    wave_evaluation: str
    overall_conditions: str
    recommendations: List[str]
    next_good_window: Optional[str]

class WeatherService:
    """Service for fetching and processing weather data"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.session = requests.Session()
        
    def fetch_marine_weather(self, lat: float, lon: float, retries: int = 2) -> Dict[str, Any]:
        """Fetch marine weather data from Open-Meteo Marine API with retry logic"""
        url = "https://marine-api.open-meteo.com/v1/marine"
        
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": [
                "wave_height", "wave_direction", "wave_period",
                "wind_wave_height", "wind_wave_direction", "wind_wave_period",
                "swell_wave_height", "swell_wave_direction", "swell_wave_period"
            ],
            "daily": [
                "wave_height_max", "wave_direction_dominant", "wave_period_max"
            ],
            "timezone": "auto",
            "forecast_days": 3
        }
        
        for attempt in range(retries + 1):
            try:
                logger.info(f"Fetching marine weather (attempt {attempt + 1})")
                response = self.session.get(url, params=params, timeout=15)
                response.raise_for_status()
                
                data = response.json()
                if not self._validate_marine_data(data):
                    raise ValueError("Invalid marine data structure")
                    
                logger.info("Successfully fetched marine weather data")
                return data
                
            except requests.exceptions.Timeout:
                logger.warning(f"Marine API timeout (attempt {attempt + 1})")
            except requests.exceptions.ConnectionError:
                logger.warning(f"Marine API connection error (attempt {attempt + 1})")
            except requests.exceptions.HTTPError as e:
                logger.error(f"Marine API HTTP error: {e}")
                break  # Don't retry on HTTP errors
            except Exception as e:
                logger.error(f"Error fetching marine weather (attempt {attempt + 1}): {e}")
                
            if attempt < retries:
                import time
                time.sleep(2 ** attempt)  # Exponential backoff
        
        logger.error("Failed to fetch marine weather after all retries")
        return self._get_fallback_marine_data()
        
    def _validate_marine_data(self, data: Dict[str, Any]) -> bool:
        """Validate marine weather data structure"""
        required_keys = ['hourly']
        if not all(key in data for key in required_keys):
            return False
        
        hourly = data.get('hourly', {})
        required_hourly = ['time', 'wave_height']
        return all(key in hourly for key in required_hourly)
    
    def _get_fallback_marine_data(self) -> Dict[str, Any]:
        """Return fallback marine data when API fails"""
        from datetime import datetime, timedelta
        base_time = datetime.now()
        times = [(base_time + timedelta(hours=i)).isoformat() for i in range(24)]
        
        return {
            "hourly": {
                "time": times,
                "wave_height": [0.5] * 24,
                "wave_period": [5.0] * 24,
                "wave_direction": [180] * 24,
                "wind_wave_height": [0.3] * 24,
                "swell_wave_height": [0.2] * 24,
                "wind_wave_period": [4.0] * 24,
                "swell_wave_period": [6.0] * 24
            }
        }
    
    def fetch_standard_weather(self, lat: float, lon: float, retries: int = 2) -> Dict[str, Any]:
        """Fetch standard weather data from Open-Meteo with retry logic"""
        url = "https://api.open-meteo.com/v1/forecast"
        
        params = {
            "latitude": lat,
            "longitude": lon,
            # Ask for current values when supported
            "current": [
                "temperature_2m", "wind_speed_10m", "wind_gusts_10m",
                "wind_direction_10m", "uv_index"
            ],
            "hourly": [
                "temperature_2m", "relative_humidity_2m", "pressure_msl",
                "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
                "visibility", "uv_index"
            ],
            "daily": [
                "temperature_2m_max", "temperature_2m_min",
                "wind_speed_10m_max", "wind_gusts_10m_max"
            ],
            # Use m/s for consistency, convert to knots in processing
            "wind_speed_unit": "ms",
            "timezone": "auto",
            "forecast_days": 3
        }
        
        for attempt in range(retries + 1):
            try:
                logger.info(f"Fetching standard weather (attempt {attempt + 1})")
                response = self.session.get(url, params=params, timeout=15)
                response.raise_for_status()
                
                data = response.json()
                if not self._validate_standard_data(data):
                    raise ValueError("Invalid standard weather data structure")
                    
                logger.info("Successfully fetched standard weather data")
                return data
                
            except requests.exceptions.Timeout:
                logger.warning(f"Standard weather API timeout (attempt {attempt + 1})")
            except requests.exceptions.ConnectionError:
                logger.warning(f"Standard weather API connection error (attempt {attempt + 1})")
            except requests.exceptions.HTTPError as e:
                logger.error(f"Standard weather API HTTP error: {e}")
                break
            except Exception as e:
                logger.error(f"Error fetching standard weather (attempt {attempt + 1}): {e}")
                
            if attempt < retries:
                import time
                time.sleep(2 ** attempt)
        
        logger.error("Failed to fetch standard weather after all retries")
        return self._get_fallback_standard_data()
    
    def _validate_standard_data(self, data: Dict[str, Any]) -> bool:
        """Validate standard weather data structure"""
        required_keys = ['hourly']
        if not all(key in data for key in required_keys):
            return False
        
        hourly = data.get('hourly', {})
        required_hourly = ['time', 'wind_speed_10m', 'temperature_2m']
        return all(key in hourly for key in required_hourly)
    
    def _get_fallback_standard_data(self) -> Dict[str, Any]:
        """Return fallback standard weather data when API fails"""
        from datetime import datetime, timedelta
        base_time = datetime.now()
        times = [(base_time + timedelta(hours=i)).isoformat() for i in range(24)]
        
        return {
            "current": {
                "temperature_2m": 20.0,
                "wind_speed_10m": 5.0,
                "wind_gusts_10m": 7.0,
                "wind_direction_10m": 180,
                "uv_index": 3.0
            },
            "hourly": {
                "time": times,
                "temperature_2m": [20.0] * 24,
                "wind_speed_10m": [5.0] * 24,
                "wind_direction_10m": [180] * 24,
                "wind_gusts_10m": [7.0] * 24,
                "relative_humidity_2m": [60] * 24,
                "pressure_msl": [1013.0] * 24,
                "visibility": [10000.0] * 24,
                "uv_index": [3.0] * 24
            },
            "utc_offset_seconds": 0
        }

    def fetch_openweather(self, lat: float, lon: float, api_key: Optional[str], retries: int = 1) -> Optional[Dict[str, Any]]:
        """Optional: fetch current wind via OpenWeather if API key provided (for cross-check)"""
        if not api_key:
            logger.info("No OpenWeather API key provided")
            return None
            
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {"lat": lat, "lon": lon, "appid": api_key, "units": "metric"}
        
        for attempt in range(retries + 1):
            try:
                logger.info(f"Fetching OpenWeather data (attempt {attempt + 1})")
                r = self.session.get(url, params=params, timeout=10)
                r.raise_for_status()
                
                data = r.json()
                if not self._validate_openweather_data(data):
                    raise ValueError("Invalid OpenWeather data structure")
                    
                logger.info("Successfully fetched OpenWeather data")
                return data
                
            except requests.exceptions.Timeout:
                logger.warning(f"OpenWeather API timeout (attempt {attempt + 1})")
            except requests.exceptions.HTTPError as e:
                logger.warning(f"OpenWeather API HTTP error: {e}")
                if e.response.status_code == 401:
                    logger.error("OpenWeather API key invalid")
                    break
            except Exception as e:
                logger.warning(f"OpenWeather fetch failed (attempt {attempt + 1}): {e}")
                
            if attempt < retries:
                import time
                time.sleep(1)
                
        logger.warning("Failed to fetch OpenWeather data after all retries")
        return None
    
    def _validate_openweather_data(self, data: Dict[str, Any]) -> bool:
        """Validate OpenWeather data structure"""
        required_keys = ['wind']
        if not all(key in data for key in required_keys):
            return False
        
        wind = data.get('wind', {})
        return 'speed' in wind

    def fetch_standard_weather_models(self, lat: float, lon: float, models: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch standard weather for a list of models (GFS, ICON, ECMWF, etc.)"""
        url = "https://api.open-meteo.com/v1/forecast"
        results: Dict[str, Dict[str, Any]] = {}
        for model in models:
            params = {
                "latitude": lat,
                "longitude": lon,
                "hourly": [
                    "temperature_2m", "wind_speed_10m", "wind_direction_10m",
                    "wind_gusts_10m"
                ],
                "timezone": "auto",
                # Some deployments of Open‑Meteo accept a `models` param with a single value
                # If not supported, the API will fallback to best match; we guard downstream
                "models": model,
                "forecast_days": 3,
                "wind_speed_unit": "kn"
            }
            try:
                response = self.session.get(url, params=params, timeout=10)
                response.raise_for_status()
                results[model] = response.json()
            except Exception as e:
                logger.warning(f"Model fetch failed for {model}: {e}")
        return results
    
    def fetch_water_temperature(self, lat: float, lon: float) -> Optional[float]:
        """Fetch water temperature from marine data"""
        # For now, we'll estimate based on location and season
        # In production, you might use a dedicated sea temperature API
        import math
        
        # Simple seasonal estimation (this is a placeholder)
        day_of_year = datetime.now().timetuple().tm_yday
        seasonal_factor = math.cos((day_of_year - 172) * 2 * math.pi / 365)
        
        # Base temperature varies by latitude
        base_temp = 15 + (30 - abs(lat)) * 0.5
        water_temp = base_temp + seasonal_factor * 8
        
        return max(5, min(30, water_temp))  # Reasonable bounds

class WingfoilAnalyzer:
    """Analyzes weather conditions for wingfoil suitability"""
    
    def __init__(self, preferences: Dict[str, Any]):
        self.preferences = preferences
        
    def evaluate_wind(self, wind_speed_knots: float, wind_direction: int, 
                     shore_direction: int = 180) -> tuple[int, str]:
        """Evaluate wind conditions for wingfoiling"""
        score = 0
        evaluation = ""
        
        # Handle None values
        if wind_speed_knots is None:
            wind_speed_knots = 0
        if wind_direction is None:
            wind_direction = 0
        
        # Wind speed evaluation - wingfoiling specific ranges
        min_wind = self.preferences.get('min_wind_knots', 8)  # Lower for wingfoiling
        max_wind = self.preferences.get('max_wind_knots', 35)  # Higher for wingfoiling
        optimal_min = self.preferences.get('optimal_wind_min', 12)
        optimal_max = self.preferences.get('optimal_wind_max', 22)
        
        if wind_speed_knots < min_wind:
            score = 0
            evaluation = f"Too light for foiling ({wind_speed_knots:.1f}kts)"
        elif wind_speed_knots > max_wind:
            score = 15
            evaluation = f"Too strong for safe foiling ({wind_speed_knots:.1f}kts)"
        elif optimal_min <= wind_speed_knots <= optimal_max:
            score = 100
            evaluation = f"Perfect foiling wind ({wind_speed_knots:.1f}kts)"
        elif min_wind <= wind_speed_knots < optimal_min:
            # Marginal but workable for experienced foilers
            score = int(60 + ((wind_speed_knots - min_wind) / (optimal_min - min_wind)) * 30)
            evaluation = f"Light but foilable ({wind_speed_knots:.1f}kts)"
        else:  # Between optimal_max and max_wind
            # Decreasing score as wind gets stronger
            score = int(100 - ((wind_speed_knots - optimal_max) / (max_wind - optimal_max)) * 70)
            evaluation = f"Strong wind, small wing needed ({wind_speed_knots:.1f}kts)"
        
        # Wind direction evaluation - more forgiving for foiling
        wind_angle_diff = abs(wind_direction - shore_direction)
        if wind_angle_diff > 180:
            wind_angle_diff = 360 - wind_angle_diff
            
        if 60 <= wind_angle_diff <= 120:  # Cross-shore winds (best for foiling)
            direction_score = 100
            direction_eval = "cross-shore (ideal)"
        elif 30 <= wind_angle_diff < 60:  # Cross-offshore
            direction_score = 90
            direction_eval = "cross-offshore (excellent)"
        elif 120 < wind_angle_diff <= 150:  # Cross-onshore
            direction_score = 85
            direction_eval = "cross-onshore (good)"
        elif wind_angle_diff < 30:  # Offshore
            direction_score = 75
            direction_eval = "offshore (manageable)"
        else:  # Onshore (150-180 degrees)
            direction_score = 50
            direction_eval = "onshore (challenging)"
        
        final_score = int((score * 0.75) + (direction_score * 0.25))
        evaluation += f", {direction_eval}"
        
        return final_score, evaluation
    
    def evaluate_waves(self, wave_height: float, wave_period: float) -> tuple[int, str]:
        """Evaluate wave conditions for wingfoiling"""
        # Handle None values
        if wave_height is None:
            wave_height = 0.5
        if wave_period is None:
            wave_period = 5.0
            
        max_wave = self.preferences.get('max_wave_height', 2.0)  # Slightly higher for foiling
        
        # Wingfoiling is more forgiving with waves due to flying above them
        if wave_height > max_wave:
            score = 30  # Still somewhat possible with good skills
            evaluation = f"Large waves ({wave_height:.1f}m) - advanced only"
        elif wave_height < 0.2:
            score = 100
            evaluation = f"Flat water ({wave_height:.1f}m) - ideal for foiling"
        elif wave_height <= 0.5:
            score = 95
            evaluation = f"Small chop ({wave_height:.1f}m) - excellent"
        elif wave_height <= 1.0:
            score = 85
            evaluation = f"Moderate waves ({wave_height:.1f}m) - good"
        elif wave_height <= 1.5:
            score = 70
            evaluation = f"Larger waves ({wave_height:.1f}m) - manageable"
        else:
            score = 50
            evaluation = f"Big waves ({wave_height:.1f}m) - challenging"
        
        # Factor in wave period for quality assessment
        if wave_period > 8:  # Long period = cleaner waves
            score = min(100, score + 10)
            evaluation += " (clean)"
        elif wave_period < 4:  # Short period = choppy
            score = max(20, score - 15)
            evaluation += " (choppy)"
        
        return score, evaluation
    
    def analyze_conditions(self, weather: WeatherConditions) -> WingfoilConditions:
        """Analyze complete weather conditions for wingfoil suitability"""
        
        wind_score, wind_eval = self.evaluate_wind(
            weather.wind_speed_knots, 
            weather.wind_direction
        )
        # Penalize gustiness: reduce wind score based on gust factor
        try:
            gust_knots_local = float(weather.wind_gust_ms) * 1.944
            base_wind_knots_local = max(float(weather.wind_speed_knots), 0.1)
            gust_factor_local = (gust_knots_local / base_wind_knots_local) if base_wind_knots_local > 0 else 1.0
        except Exception:
            gust_factor_local = 1.0
        # Discrete penalty curve for gustiness (heavier penalty for very gusty)
        if gust_factor_local <= 1.10:
            gust_penalty_points = 0
            gust_label = "steady"
        elif gust_factor_local <= 1.25:
            gust_penalty_points = 10
            gust_label = "moderately gusty"
        elif gust_factor_local <= 1.40:
            gust_penalty_points = 20
            gust_label = "gusty"
        elif gust_factor_local <= 1.60:
            gust_penalty_points = 30
            gust_label = "very gusty"
        else:
            gust_penalty_points = 40
            gust_label = "extremely gusty"
        wind_score = max(0, int(wind_score - gust_penalty_points))
        wind_eval = f"{wind_eval}, {gust_label} (gust factor {gust_factor_local:.2f})"
        
        wave_score, wave_eval = self.evaluate_waves(
            weather.wave_height, 
            weather.wave_period
        )
        
        # Overall score (weighted average)
        overall_score = int((wind_score * 0.8) + (wave_score * 0.2))
        
        # Determine suitability
        suitable = overall_score >= 60
        
        # Generate wingfoil-specific recommendations
        recommendations = []
        
        # Wind-based recommendations
        if wind_score < 40:
            recommendations.append("Wind too light for foiling - wait for better conditions")
        elif wind_score < 60:
            recommendations.append("Light wind - use larger wing and light board for early planing")
        elif weather.wind_speed_knots > 30:
            recommendations.append("Very strong wind - use smallest wing and consider safety")
        elif weather.wind_speed_knots > 25:
            recommendations.append("Strong wind - use smaller wing (3-4m) and stable foil")
        elif weather.wind_speed_knots < 10:
            recommendations.append("Very light wind - large wing (6-7m) and low-end foil needed")
        
        # Wave-based recommendations
        if wave_score < 50:
            recommendations.append("Rough conditions - choose sheltered spots or consider smaller foil")
        elif weather.wave_height > 1.5:
            recommendations.append("Large waves - use stable foil and stay upwind")
        elif weather.wave_height < 0.3:
            recommendations.append("Flat water - perfect for learning and freestyle")
        
        # Temperature recommendations
        if weather.temperature < 10:
            recommendations.append("Cold conditions - bring 4/3mm wetsuit or drysuit")
        elif weather.temperature < 15:
            recommendations.append("Cool conditions - 3/2mm wetsuit recommended")
        elif weather.temperature > 25:
            recommendations.append("Warm conditions - perfect for learning, stay hydrated")
        
        # Gust factor recommendations
        gust_factor = (weather.wind_gust_ms * 1.944) / max(weather.wind_speed_knots, 1)
        if gust_factor > 1.4:
            recommendations.append("Gusty conditions - be prepared for power management")
        
        # UV recommendations
        if weather.uv_index > 7:
            recommendations.append("High UV - wear sun protection and consider shade breaks")
        
        # Limit to most important recommendations
        recommendations = recommendations[:4]
        
        # Overall condition description
        if overall_score >= 85:
            overall_conditions = "Excellent"
        elif overall_score >= 70:
            overall_conditions = "Good"
        elif overall_score >= 60:
            overall_conditions = "Marginal"
        else:
            overall_conditions = "Poor"
        
        return WingfoilConditions(
            suitable=suitable,
            score=overall_score,
            wind_evaluation=wind_eval,
            wave_evaluation=wave_eval,
            overall_conditions=overall_conditions,
            recommendations=recommendations,
            next_good_window=None  # TODO: Implement forecast analysis
        )


class WingfoilAdvisor:
    """Provides wingfoil-specific recommendations based on conditions and rider profile"""

    def __init__(self, preferences: Dict[str, Any], user: Dict[str, Any]):
        self.preferences = preferences
        self.user = user or {}

    def recommend_wing_size(self, wind_knots: float) -> Tuple[str, List[str]]:
        weight = float(self.user.get("rider_weight_kg", 80))
        skill = (self.user.get("skill_level", "intermediate") or "intermediate").lower()

        # Enhanced wing sizing for wingfoiling (more precise ranges)
        if wind_knots < 8:
            size = "7-8m"
            wind_desc = "very light"
        elif wind_knots < 12:
            size = "6-7m"
            wind_desc = "light"
        elif wind_knots < 16:
            size = "5-6m"
            wind_desc = "moderate"
        elif wind_knots < 20:
            size = "4-5m"
            wind_desc = "fresh"
        elif wind_knots < 25:
            size = "3.5-4m"
            wind_desc = "strong"
        elif wind_knots < 30:
            size = "3-3.5m"
            wind_desc = "very strong"
        else:
            size = "2.5-3m"
            wind_desc = "extreme"

        notes: List[str] = []
        
        # Weight adjustments (more detailed)
        if weight >= 100:
            notes.append("Heavy rider (100kg+): size up 1-1.5m")
        elif weight >= 85:
            notes.append("Heavy rider (85kg+): size up 0.5-1m")
        elif weight <= 60:
            notes.append("Light rider (60kg-): size down 0.5-1m")
        elif weight <= 70:
            notes.append("Light rider (70kg-): size down 0.5m")

        # Skill adjustments
        if skill in ("beginner", "novice"):
            notes.append("Beginner: use larger stable wing, avoid gusty conditions")
        elif skill == "advanced":
            notes.append("Advanced: can handle smaller wings in marginal conditions")

        # Wind-specific advice
        if wind_knots < 10:
            notes.append(f"Light wind ({wind_desc}): use largest wing and light equipment")
        elif wind_knots > 25:
            notes.append(f"Strong wind ({wind_desc}): prioritize safety and control")

        return size, notes

    def compute_advice(self, weather: WeatherConditions) -> Dict[str, Any]:
        gust_knots = float(weather.wind_gust_ms) * 1.944
        gust_factor = (gust_knots / weather.wind_speed_knots) if weather.wind_speed_knots > 0 else 1.0
        wing_size, notes = self.recommend_wing_size(weather.wind_speed_knots)
        
        # Equipment recommendations
        equipment_advice = []
        
        # Foil recommendations based on conditions
        if weather.wind_speed_knots < 12:
            equipment_advice.append("Low-wind foil: large front wing (1000-1400cm²)")
        elif weather.wind_speed_knots > 20:
            equipment_advice.append("High-wind foil: smaller front wing (600-900cm²)")
        else:
            equipment_advice.append("All-round foil: medium front wing (800-1200cm²)")
        
        # Board recommendations
        skill = self.user.get("skill_level", "intermediate").lower()
        if skill in ("beginner", "novice"):
            equipment_advice.append("Board: 80-120L, stable and wide")
        else:
            equipment_advice.append("Board: 60-90L based on conditions")
        
        # Session recommendations
        session_advice = []
        if gust_factor > 1.3:
            session_advice.append("Gusty conditions: practice power management")
        if weather.wave_height > 1.0:
            session_advice.append("Waves present: practice wave riding skills")
        if weather.temperature < 15:
            session_advice.append("Cold water: consider shorter sessions")
        
        # Safety recommendations
        safety_advice = []
        if weather.wind_speed_knots > 25:
            safety_advice.append("Strong wind: stay close to shore, use impact vest")
        if weather.visibility < 5000:  # 5km
            safety_advice.append("Poor visibility: stay near launch area")
        if weather.uv_index > 6:
            safety_advice.append("High UV: use sun protection")

        advice = {
            "recommended_wing_size": wing_size,
            "gust_factor": round(gust_factor, 2),
            "sizing_notes": notes,
            "equipment_advice": equipment_advice[:2],  # Limit to most important
            "session_advice": session_advice[:2],
            "safety_advice": safety_advice[:2],
            "conditions_summary": self._generate_conditions_summary(weather)
        }
        return advice
        
    def _generate_conditions_summary(self, weather: WeatherConditions) -> str:
        """Generate a concise summary of conditions for the session"""
        wind_desc = "light" if weather.wind_speed_knots < 12 else \
                   "moderate" if weather.wind_speed_knots < 18 else \
                   "strong" if weather.wind_speed_knots < 25 else \
                   "very strong"
        
        wave_desc = "flat" if weather.wave_height < 0.3 else \
                   "small waves" if weather.wave_height < 1.0 else \
                   "moderate waves" if weather.wave_height < 1.5 else \
                   "large waves"
        
        temp_desc = "cold" if weather.temperature < 12 else \
                   "cool" if weather.temperature < 18 else \
                   "mild" if weather.temperature < 24 else \
                   "warm"
        
        return f"{wind_desc.title()} wind, {wave_desc}, {temp_desc} conditions"

# Global services
weather_service = None
wingfoil_analyzer = None
wingfoil_advisor = None

def load_config():
    """Load configuration from file"""
    config_path = '/app/config/config.json'
    default_config = {
        "location": {
            "name": "Default Location",
            "latitude": 52.5200,  # Berlin as default
            "longitude": 13.4050,
            "shore_direction": 180
        },
        "wingfoil_preferences": {
            "min_wind_knots": 12,
            "max_wind_knots": 30,
            "optimal_wind_min": 15,
            "optimal_wind_max": 25,
            "max_wave_height": 1.5
        },
        "update_interval_minutes": 30
    }
    
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = json.load(f)
                # Merge with defaults
                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                return config
        else:
            return default_config
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return default_config

def _sanitize_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return a redacted version of the config that is safe to return to clients."""
    try:
        import copy
        safe = copy.deepcopy(cfg or {})
        # Redact known integration keys
        integrations = safe.get('integrations') or {}
        for k, v in list(integrations.items()):
            if isinstance(v, str) and v:
                integrations[k] = 'REDACTED'
        safe['integrations'] = integrations
        # Redact any admin token if present
        api_settings = safe.get('api_settings') or {}
        if 'admin_token' in api_settings:
            api_settings['admin_token'] = 'REDACTED'
        safe['api_settings'] = api_settings
        return safe
    except Exception:
        return {}

def _require_admin(request_obj) -> bool:
    """Optional admin guard for mutating endpoints.
    If an admin token is configured (env API_ADMIN_TOKEN or config.api_settings.admin_token),
    the request must include header X-Admin-Token with a matching value.
    If no token is configured, allow (assumes upstream auth/proxy).
    """
    try:
        token = os.getenv('API_ADMIN_TOKEN')
        if not token:
            cfg = load_config()
            token = (cfg.get('api_settings') or {}).get('admin_token')
        if not token:
            return True  # no token configured; rely on upstream protection
        provided = request_obj.headers.get('X-Admin-Token')
        import hmac
        return provided is not None and hmac.compare_digest(str(provided), str(token))
    except Exception:
        return False

def init_services():
    """Initialize global services"""
    global weather_service, wingfoil_analyzer, wingfoil_advisor
    
    config = load_config()
    weather_service = WeatherService(config)
    wingfoil_analyzer = WingfoilAnalyzer(config['wingfoil_preferences'])
    wingfoil_advisor = WingfoilAdvisor(config.get('wingfoil_preferences', {}), config.get('user', {}))

@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')

@app.route('/api/current-conditions')
def get_current_conditions():
    """API endpoint for current weather and wingsurf conditions"""
    try:
        config = load_config()
        location = config['location']
        
        # Fetch weather data with enhanced error handling
        try:
            marine_data = weather_service.fetch_marine_weather(
                location['latitude'], location['longitude']
            )
            standard_data = weather_service.fetch_standard_weather(
                location['latitude'], location['longitude']
            )
            
            # Validate that we have usable data
            if not marine_data or not marine_data.get('hourly'):
                logger.warning("Invalid marine data received, using fallback")
                marine_data = weather_service._get_fallback_marine_data()
                
            if not standard_data or not standard_data.get('hourly'):
                logger.warning("Invalid standard data received, using fallback")
                standard_data = weather_service._get_fallback_standard_data()
                
        except Exception as e:
            logger.error(f"Critical error fetching weather data: {e}")
            return jsonify({
                "error": "Weather service unavailable", 
                "details": "Using fallback data",
                "fallback": True
            }), 503
        
        # Extract current conditions (first hour of forecast)
        current_time = datetime.now()
        
        # Get arrays
        hourly_standard = standard_data.get('hourly', {})
        hourly_marine = marine_data.get('hourly', {})

        # Determine best index for "now" in the provider's local timezone
        tz_offset_sec = int(standard_data.get('utc_offset_seconds') or 0)
        now_provider = datetime.utcnow().replace(tzinfo=timezone.utc) + timedelta(seconds=tz_offset_sec)

        def nearest_index(times: List[str]) -> int:
            if not times:
                return 0
            try:
                best_i, best_delta = 0, 10**9
                for i, t in enumerate(times):
                    dt = dateparser.isoparse(t)
                    # If times are naive, assume provider's local
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=None)
                    delta = abs((dt - now_provider.replace(tzinfo=None)).total_seconds())
                    if delta < best_delta:
                        best_delta, best_i = delta, i
                return best_i
            except Exception:
                return 0

        std_times: List[str] = hourly_standard.get('time') or []
        mar_times: List[str] = hourly_marine.get('time') or []
        idx_std = nearest_index(std_times)
        idx_mar = nearest_index(mar_times)
        
        # Helper functions to sanitize upstream values
        def safe_get_first(data_dict, key, default=0):
            values = data_dict.get(key)
            if not values or len(values) == 0 or values[0] is None:
                return default
            return values[0]

        def as_float(value, default=0.0):
            try:
                if value is None:
                    return default
                return float(value)
            except Exception:
                return default

        def as_int(value, default=0):
            try:
                if value is None:
                    return default
                return int(round(float(value)))
            except Exception:
                return default
        
        # Use `current` block if present (more accurate), else nearest hourly index
        current_block = standard_data.get('current') or {}
        wind_speed_ms = as_float(current_block.get('wind_speed_10m'), None)
        if wind_speed_ms is None:
            wind_speed_ms = as_float((hourly_standard.get('wind_speed_10m') or [0])[idx_std], 0.0)
        wind_direction = as_int(current_block.get('wind_direction_10m'), None)
        if wind_direction is None:
            wind_direction = as_int((hourly_standard.get('wind_direction_10m') or [0])[idx_std], 0)
        temperature = as_float(current_block.get('temperature_2m'), None)
        if temperature is None:
            temperature = as_float((hourly_standard.get('temperature_2m') or [15])[idx_std], 15.0)
        uv_index_val = as_float(current_block.get('uv_index'), None)
        if uv_index_val is None:
            uv_index_val = as_float((hourly_standard.get('uv_index') or [0])[idx_std], 0.0)

        wave_height = as_float((hourly_marine.get('wave_height') or [0.5])[idx_mar], 0.5)
        wave_period = as_float((hourly_marine.get('wave_period') or [5.0])[idx_mar], 5.0)
        wind_wave_h = as_float((hourly_marine.get('wind_wave_height') or [0.0])[idx_mar], 0.0)
        swell_wave_h = as_float((hourly_marine.get('swell_wave_height') or [0.0])[idx_mar], 0.0)
        wind_wave_p = as_float((hourly_marine.get('wind_wave_period') or [0.0])[idx_mar], 0.0)
        swell_wave_p = as_float((hourly_marine.get('swell_wave_period') or [0.0])[idx_mar], 0.0)
        
        weather_conditions = WeatherConditions(
            timestamp=current_time.isoformat(),
            location=location['name'],
            latitude=location['latitude'],
            longitude=location['longitude'],
            wind_speed_ms=wind_speed_ms,
            wind_speed_knots=wind_speed_ms * 1.944,  # m/s to knots
            wind_direction=wind_direction,
            wind_gust_ms=as_float(safe_get_first(hourly_standard, 'wind_gusts_10m', wind_speed_ms), wind_speed_ms),
            temperature=temperature,
            water_temperature=weather_service.fetch_water_temperature(
                location['latitude'], location['longitude']
            ) or 15.0,
            wave_height=wave_height,
            wave_period=wave_period,
            wave_direction=as_int(safe_get_first(hourly_marine, 'wave_direction', 180), 180),
            pressure=as_float(safe_get_first(hourly_standard, 'pressure_msl', 1013), 1013.0),
            humidity=as_int(safe_get_first(hourly_standard, 'relative_humidity_2m', 50), 50),
            visibility=as_float(safe_get_first(hourly_standard, 'visibility', 10000), 10000.0),
            uv_index=uv_index_val,
            # Derived sport metrics
            shore_angle_deg=as_int(abs(wind_direction - int(location.get('shore_direction', 180))) % 360, 0),
            chop_index=as_float(((wind_wave_h + 0.01) / (swell_wave_h + 0.01)), 0.0)
        )

        # Multi-model consensus (best-effort; API may ignore models param)
        models_to_try = config.get('models', ['gfs', 'icon_seamless', 'ecmwf_ifs04'])
        model_results = weather_service.fetch_standard_weather_models(location['latitude'], location['longitude'], models_to_try)

        # Optional: OpenWeather current wind for cross-check
        openweather_key = load_config().get('integrations', {}).get('openweather_api_key')
        ow = weather_service.fetch_openweather(location['latitude'], location['longitude'], openweather_key)
        if ow:
            try:
                ow_speed_ms = float(ow['wind']['speed'])
                ow_gust_ms = float(ow['wind'].get('gust', ow_speed_ms))
                model_results['openweather'] = {
                    'hourly': {},
                    'current': {
                        'wind_speed_10m': ow_speed_ms,
                        'wind_gusts_10m': ow_gust_ms
                    }
                }
            except Exception:
                pass

        # Enhanced data averaging between OpenWeather and Open-Meteo
        def average_values(open_meteo_val: float, openweather_val: Optional[float]) -> float:
            """Average values from Open-Meteo and OpenWeather, with fallback to Open-Meteo"""
            if openweather_val is not None and open_meteo_val is not None:
                # Weight Open-Meteo slightly higher due to marine-specific data
                return (open_meteo_val * 0.6) + (openweather_val * 0.4)
            return open_meteo_val
            
        # Enhanced wind data with OpenWeather averaging
        enhanced_wind_speed_ms = wind_speed_ms
        enhanced_gust_ms = weather_conditions.wind_gust_ms
        
        if ow:
            try:
                ow_speed_ms = float(ow['wind']['speed'])
                ow_gust_ms = float(ow['wind'].get('gust', ow_speed_ms))
                enhanced_wind_speed_ms = average_values(wind_speed_ms, ow_speed_ms)
                enhanced_gust_ms = average_values(weather_conditions.wind_gust_ms, ow_gust_ms)
                logger.info(f"Averaged wind data: Open-Meteo {wind_speed_ms:.1f}m/s, OpenWeather {ow_speed_ms:.1f}m/s, Result {enhanced_wind_speed_ms:.1f}m/s")
            except Exception as e:
                logger.warning(f"Error processing OpenWeather data for averaging: {e}")
        
        # Update weather conditions with enhanced values
        weather_conditions.wind_speed_ms = enhanced_wind_speed_ms
        weather_conditions.wind_speed_knots = enhanced_wind_speed_ms * 1.944
        weather_conditions.wind_gust_ms = enhanced_gust_ms
        
        # Update shore angle calculation with any potential wind direction changes
        weather_conditions.shore_angle_deg = as_int(abs(wind_direction - int(location.get('shore_direction', 180))) % 360, 0)
        
        # Now analyze wingfoil conditions with enhanced wind data
        wingfoil_conditions = wingfoil_analyzer.analyze_conditions(weather_conditions)
        wingfoil_advice = wingfoil_advisor.compute_advice(weather_conditions)

        def collect_model_value(model_data: Dict[str, Any], key: str, default: float = 0.0) -> float:
            hourly = (model_data or {}).get('hourly', {})
            v = safe_get_first(hourly, key, default)
            return as_float(v, default)

        values_speed, values_gust = [], []
        weights_speed, weights_gust = [], []
        per_model: Dict[str, Any] = {}
        # Optional model weights from config
        model_weights: Dict[str, float] = (config.get('model_weights') or {})
        for model_name, payload in model_results.items():
            sp = collect_model_value(payload, 'wind_speed_10m', wind_speed_ms)
            gu = collect_model_value(payload, 'wind_gusts_10m', weather_conditions.wind_gust_ms)
            per_model[model_name] = {
                'wind_speed_knots': round(sp * 1.944, 1),
                'wind_gust_knots': round(gu * 1.944, 1),
            }
            values_speed.append(sp)
            values_gust.append(gu)
            # Default weight 1.0 if not configured
            w = float(model_weights.get(model_name, 1.0))
            weights_speed.append(w)
            weights_gust.append(w)

        def median(lst: List[float]) -> float:
            s = sorted(lst)
            if not s:
                return 0.0
            n = len(s)
            return (s[n//2] if n % 2 == 1 else (s[n//2-1] + s[n//2]) / 2)

        def weighted_mean(values: List[float], weights: List[float]) -> float:
            if not values:
                return 0.0
            if not weights or len(weights) != len(values):
                weights = [1.0] * len(values)
            total_w = sum(weights)
            if total_w <= 0:
                weights = [1.0] * len(values)
                total_w = float(len(values))
            return sum(v * w for v, w in zip(values, weights)) / total_w

        # Compute consensus and derived gust factor
        consensus = {
            'models_used': list(per_model.keys()),
            'weights_used': {k: float(model_weights.get(k, 1.0)) for k in per_model.keys()},
            'median_wind_knots': round(median(values_speed) * 1.944, 1) if values_speed else round(weather_conditions.wind_speed_knots, 1),
            'median_gust_knots': round(median(values_gust) * 1.944, 1) if values_gust else round(float(weather_conditions.wind_gust_ms) * 1.944, 1),
            'weighted_wind_knots': round(weighted_mean(values_speed, weights_speed) * 1.944, 1) if values_speed else round(weather_conditions.wind_speed_knots, 1),
            'weighted_gust_knots': round(weighted_mean(values_gust, weights_gust) * 1.944, 1) if values_gust else round(float(weather_conditions.wind_gust_ms) * 1.944, 1),
            'spread_knots': round((max(values_speed) - min(values_speed)) * 1.944, 1) if len(values_speed) >= 2 else 0.0
        }
        try:
            m_wind = max(consensus['median_wind_knots'], 0.1)
            consensus['gust_factor'] = round(consensus['median_gust_knots'] / m_wind, 2)
        except Exception:
            consensus['gust_factor'] = 1.0
        
        # UI/display settings to help client render overlays
        ui_settings = {
            "map_overlay": {
                "shoreline_length_pct": int((config.get('display_settings') or {}).get('map_overlay', {}).get('shoreline_length_pct', 35))
            }
        }

        return jsonify({
            "weather": asdict(weather_conditions),
            "wingfoil": asdict(wingfoil_conditions),
            "wingfoil_advice": wingfoil_advice,
            "display_settings": ui_settings,
            "sport_metrics": {
                "shore_angle_deg": weather_conditions.shore_angle_deg,
                "shore_direction_deg": int(location.get('shore_direction', 180)),
                "wind_to_shore_angle_deg": weather_conditions.shore_angle_deg,
                "chop_index": round(weather_conditions.chop_index, 2),
                "wind_wave_height": wind_wave_h,
                "swell_wave_height": swell_wave_h,
                "wind_wave_period": wind_wave_p,
                "swell_wave_period": swell_wave_p
            },
            "multi_model": {
                "per_model": per_model,
                "consensus": consensus
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting current conditions: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/inkypi/morning-report')
def get_inkypi_morning_report():
    """
    Special endpoint for InkyPi morning reports
    
    Returns simplified, formatted data optimized for e-ink display
    """
    try:
        # Use daily summary for the day plan + current for snapshot
        daily_res = get_daily_summary()
        if daily_res.status_code != 200:
            return daily_res
        daily = daily_res.get_json()

        current_res = get_current_conditions()
        if current_res.status_code != 200:
            return current_res
        current = current_res.get_json()
        weather = current['weather']
        wingfoil = current['wingfoil']
        wingfoil_advice = current.get('wingfoil_advice', {})
        
        # Safe formatting helpers
        def fmt_num(value, unit="", digits=1):
            try:
                return f"{float(value):.{digits}f}{unit}"
            except Exception:
                return "N/A"

        # Format for InkyPi display (robust to missing values)
        morning_report = {
            "title": "Morning Wingfoil Report",
            "location": weather.get('location', 'Unknown'),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "conditions": {
                "wind": f"{fmt_num(weather.get('wind_speed_knots'), ' knots')} @ {weather.get('wind_direction', '—')}°",
                "waves": f"{fmt_num(weather.get('wave_height'), 'm')} / {fmt_num(weather.get('wave_period'), 's')}",
                "air_temp": f"{fmt_num(weather.get('temperature'), '°C')}",
                "water_temp": f"{fmt_num(weather.get('water_temperature'), '°C')}",
                "pressure": f"{fmt_num(weather.get('pressure'), ' hPa', digits=0)}"
            },
            "wingfoil_assessment": {
                "suitable": bool(wingfoil.get('suitable', False)),
                "score": int(wingfoil.get('score', 0)),
                "condition": wingfoil.get('overall_conditions', 'Unknown'),
                "wind_eval": wingfoil.get('wind_evaluation', 'N/A'),
                "wave_eval": wingfoil.get('wave_evaluation', 'N/A')
            },
            "wingfoil_advice": {
                "recommended_wing_size": wingfoil_advice.get('recommended_wing_size', '—'),
                "gust_factor": wingfoil_advice.get('gust_factor', '—')
            },
            "day_plan": {
                "day": daily.get('day'),
                "wind": daily.get('wind_knots'),
                "gust": daily.get('gust_knots'),
                "temp": daily.get('temperature_c'),
                "waves": daily.get('wave_height_m'),
                "optimal_windows": daily.get('optimal_windows', [])
            },
            "recommendations": (wingfoil.get('recommendations') or [])[:3],
            "summary": f"Wingfoil conditions: {wingfoil.get('overall_conditions', 'Unknown')} ({wingfoil.get('score', 0)}/100)"
        }
        
        return jsonify(morning_report)
        
    except Exception as e:
        logger.error(f"Error generating morning report: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/forecast/<int:hours>')
def get_forecast(hours: int):
    """Get forecast for next N hours"""
    # TODO: Implement detailed forecast
    return jsonify({"message": f"Forecast for next {hours} hours - coming soon!"})

@app.route('/api/hourly-forecast')
def get_hourly_forecast():
    """Get hourly forecast for the current day"""
    try:
        config = load_config()
        location = config['location']
        
        # Fetch weather data with enhanced error handling
        try:
            marine_data = weather_service.fetch_marine_weather(
                location['latitude'], location['longitude']
            )
            standard_data = weather_service.fetch_standard_weather(
                location['latitude'], location['longitude']
            )
            
            # Validate that we have usable data
            if not marine_data or not marine_data.get('hourly'):
                logger.warning("Invalid marine data received for hourly forecast, using fallback")
                marine_data = weather_service._get_fallback_marine_data()
                
            if not standard_data or not standard_data.get('hourly'):
                logger.warning("Invalid standard data received for hourly forecast, using fallback")
                standard_data = weather_service._get_fallback_standard_data()
                
        except Exception as e:
            logger.error(f"Critical error fetching weather data for hourly forecast: {e}")
            return jsonify({
                "error": "Weather service unavailable", 
                "details": "Using fallback data",
                "fallback": True
            }), 503
        
        # Get timezone info
        tz_offset_sec = int(standard_data.get('utc_offset_seconds') or 0)
        local_now = datetime.utcnow() + timedelta(seconds=tz_offset_sec)
        local_day = local_now.date()
        
        # Get hourly data
        hourly_standard = standard_data.get('hourly', {})
        hourly_marine = marine_data.get('hourly', {})
        
        times: List[str] = hourly_standard.get('time') or []
        
        # Filter for today's hours only (excluding night hours 22:00-04:00)
        today_indices = []
        today_times = []
        for i, time_str in enumerate(times):
            try:
                dt = dateparser.isoparse(time_str)
                if dt.date() == local_day:
                    hour = dt.hour
                    # Exclude night hours (22:00-04:00)
                    if not (hour >= 22 or hour <= 4):
                        today_indices.append(i)
                        today_times.append(time_str)
            except Exception:
                continue
        
        # Helper functions
        def safe_get_hourly(data_dict, key: str, indices: List[int], default=0):
            values = data_dict.get(key, [])
            result = []
            for i in indices:
                if i < len(values) and values[i] is not None:
                    try:
                        result.append(float(values[i]))
                    except (ValueError, TypeError):
                        result.append(default)
                else:
                    result.append(default)
            return result
        
        # Get hourly values for today
        wind_speeds_ms = safe_get_hourly(hourly_standard, 'wind_speed_10m', today_indices, 0.0)
        wind_directions = safe_get_hourly(hourly_standard, 'wind_direction_10m', today_indices, 180)
        wind_gusts_ms = safe_get_hourly(hourly_standard, 'wind_gusts_10m', today_indices, 0.0)
        temperatures = safe_get_hourly(hourly_standard, 'temperature_2m', today_indices, 20.0)
        pressures = safe_get_hourly(hourly_standard, 'pressure_msl', today_indices, 1013.0)
        humidity = safe_get_hourly(hourly_standard, 'relative_humidity_2m', today_indices, 60)
        uv_indices = safe_get_hourly(hourly_standard, 'uv_index', today_indices, 0.0)
        
        # Marine data (may have different time intervals)
        marine_times = hourly_marine.get('time', [])
        marine_today_indices = []
        for i, time_str in enumerate(marine_times):
            try:
                dt = dateparser.isoparse(time_str)
                if dt.date() == local_day:
                    marine_today_indices.append(i)
            except Exception:
                continue
        
        wave_heights = safe_get_hourly(hourly_marine, 'wave_height', marine_today_indices, 0.5)
        wave_periods = safe_get_hourly(hourly_marine, 'wave_period', marine_today_indices, 5.0)
        wave_directions = safe_get_hourly(hourly_marine, 'wave_direction', marine_today_indices, 180)
        
        # Create hourly forecast data
        hourly_forecast = []
        
        for i, time_str in enumerate(today_times):
            try:
                dt = dateparser.isoparse(time_str)
                hour_display = dt.strftime("%H:%M")
                
                # Get values for this hour
                wind_speed_ms = wind_speeds_ms[i] if i < len(wind_speeds_ms) else 0.0
                wind_speed_knots = wind_speed_ms * 1.944
                wind_dir = int(wind_directions[i]) if i < len(wind_directions) else 180
                wind_gust_ms = wind_gusts_ms[i] if i < len(wind_gusts_ms) else wind_speed_ms
                temp = temperatures[i] if i < len(temperatures) else 20.0
                
                # Marine data (interpolate if needed since marine data might be less frequent)
                marine_index = min(i, len(wave_heights) - 1) if wave_heights else 0
                wave_height = wave_heights[marine_index] if wave_heights else 0.5
                wave_period = wave_periods[marine_index] if wave_periods else 5.0
                
                # Create weather conditions for this hour
                hour_conditions = WeatherConditions(
                    timestamp=time_str,
                    location=location['name'],
                    latitude=location['latitude'],
                    longitude=location['longitude'],
                    wind_speed_ms=wind_speed_ms,
                    wind_speed_knots=wind_speed_knots,
                    wind_direction=wind_dir,
                    wind_gust_ms=wind_gust_ms,
                    temperature=temp,
                    water_temperature=15.0,  # Use default for hourly
                    wave_height=wave_height,
                    wave_period=wave_period,
                    wave_direction=180,  # Default
                    pressure=pressures[i] if i < len(pressures) else 1013.0,
                    humidity=int(humidity[i]) if i < len(humidity) else 60,
                    visibility=10000.0,  # Default
                    uv_index=uv_indices[i] if i < len(uv_indices) else 0.0,
                    shore_angle_deg=abs(wind_dir - location.get('shore_direction', 180)) % 360,
                    chop_index=1.0  # Default
                )
                
                # Simple wingfoil analysis for this hour
                try:
                    # Get wingfoil preferences
                    prefs = config.get('wingfoil_preferences', {})
                    min_wind = prefs.get('min_wind_knots', 8)
                    max_wind = prefs.get('max_wind_knots', 35)
                    optimal_min = prefs.get('optimal_wind_min', 12)
                    optimal_max = prefs.get('optimal_wind_max', 22)
                    
                    # Simple wind scoring
                    if wind_speed_knots < min_wind:
                        wind_score = 0
                        wind_eval = f"Too light ({wind_speed_knots:.1f}kts)"
                    elif wind_speed_knots > max_wind:
                        wind_score = 15
                        wind_eval = f"Too strong ({wind_speed_knots:.1f}kts)"
                    elif optimal_min <= wind_speed_knots <= optimal_max:
                        wind_score = 100
                        wind_eval = f"Perfect ({wind_speed_knots:.1f}kts)"
                    else:
                        wind_score = 70
                        wind_eval = f"Acceptable ({wind_speed_knots:.1f}kts)"
                    # Gustiness penalty
                    try:
                        gust_knots_h = float(wind_gust_ms) * 1.944
                        base_knots_h = max(float(wind_speed_knots), 0.1)
                        gust_factor_h = (gust_knots_h / base_knots_h) if base_knots_h > 0 else 1.0
                    except Exception:
                        gust_factor_h = 1.0
                    if gust_factor_h > 1.10:
                        if gust_factor_h <= 1.25:
                            wind_score -= 10
                            wind_eval += ", moderately gusty"
                        elif gust_factor_h <= 1.40:
                            wind_score -= 20
                            wind_eval += ", gusty"
                        elif gust_factor_h <= 1.60:
                            wind_score -= 30
                            wind_eval += ", very gusty"
                        else:
                            wind_score -= 40
                            wind_eval += ", extremely gusty"
                        wind_score = max(0, int(wind_score))
                    
                    # Simple wave scoring
                    if wave_height > 2.0:
                        wave_score = 30
                    elif wave_height < 0.2:
                        wave_score = 100
                    else:
                        wave_score = 85
                    
                    # Overall score
                    overall_score = int((wind_score * 0.8) + (wave_score * 0.2))
                    
                    # Overall conditions
                    if overall_score >= 85:
                        overall_conditions = "Excellent"
                    elif overall_score >= 70:
                        overall_conditions = "Good"
                    elif overall_score >= 60:
                        overall_conditions = "Marginal"
                    else:
                        overall_conditions = "Poor"
                    
                    # Weight-specific wing size recommendation
                    rider_weight = config.get('user', {}).get('rider_weight_kg', 80)
                    
                    # Base wing sizes for ~80kg rider
                    if wind_speed_knots < 8:
                        base_size = "7-8m"
                    elif wind_speed_knots < 12:
                        base_size = "6-7m"
                    elif wind_speed_knots < 16:
                        base_size = "5-6m"
                    elif wind_speed_knots < 20:
                        base_size = "4-5m"
                    elif wind_speed_knots < 25:
                        base_size = "3.5-4m"
                    else:
                        base_size = "3m"
                    
                    # Adjust for rider weight
                    if rider_weight >= 90:
                        if wind_speed_knots < 8:
                            wing_size = "8-9m"
                        elif wind_speed_knots < 12:
                            wing_size = "7-8m"
                        elif wind_speed_knots < 16:
                            wing_size = "6-7m"
                        elif wind_speed_knots < 20:
                            wing_size = "5-6m"
                        elif wind_speed_knots < 25:
                            wing_size = "4-5m"
                        else:
                            wing_size = "3.5-4m"
                    elif rider_weight <= 65:
                        if wind_speed_knots < 8:
                            wing_size = "6-7m"
                        elif wind_speed_knots < 12:
                            wing_size = "5-6m"
                        elif wind_speed_knots < 16:
                            wing_size = "4-5m"
                        elif wind_speed_knots < 20:
                            wing_size = "3.5-4m"
                        elif wind_speed_knots < 25:
                            wing_size = "3m"
                        else:
                            wing_size = "2.5-3m"
                    else:
                        wing_size = base_size
                    
                    wingfoil_data = {
                        "score": overall_score,
                        "suitable": overall_score >= 60,
                        "overall_conditions": overall_conditions,
                        "wind_evaluation": wind_eval,
                        "wing_size": wing_size
                    }
                except Exception as e:
                    logger.warning(f"Error analyzing wingfoil conditions for hour {hour_display}: {e}")
                    wingfoil_data = {
                        "score": 0,
                        "suitable": False,
                        "overall_conditions": "Analysis Error",
                        "wind_evaluation": "N/A",
                        "wing_size": "N/A"
                    }
                
                # Create summary for this hour
                hour_summary = {
                    "time": hour_display,
                    "timestamp": time_str,
                    "wind": {
                        "speed_knots": round(wind_speed_knots, 1),
                        "direction": wind_dir,
                        "gust_knots": round(wind_gust_ms * 1.944, 1)
                    },
                    "waves": {
                        "height_m": round(wave_height, 1),
                        "period_s": round(wave_period, 1)
                    },
                    "conditions": {
                        "temperature": round(temp, 1),
                        "uv_index": round(uv_indices[i] if i < len(uv_indices) else 0.0, 1),
                        "pressure": round(pressures[i] if i < len(pressures) else 1013.0, 0)
                    },
                    "wingfoil": wingfoil_data
                }
                
                hourly_forecast.append(hour_summary)
                
            except Exception as e:
                logger.warning(f"Error processing hour {i}: {e}")
                continue
        
        return jsonify({
            "date": str(local_day),
            "location": location['name'],
            "hourly_forecast": hourly_forecast,
            "summary": {
                "total_hours": len(hourly_forecast),
                "good_hours": len([h for h in hourly_forecast if h['wingfoil']['score'] >= 70]),
                "suitable_hours": len([h for h in hourly_forecast if h['wingfoil']['suitable']])
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting hourly forecast: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tomorrow-forecast')
def get_tomorrow_forecast():
    """Get hourly forecast for tomorrow (excluding night hours)"""
    try:
        config = load_config()
        location = config['location']
        
        # Fetch weather data
        try:
            marine_data = weather_service.fetch_marine_weather(
                location['latitude'], location['longitude']
            )
            standard_data = weather_service.fetch_standard_weather(
                location['latitude'], location['longitude']
            )
            
            if not marine_data or not marine_data.get('hourly'):
                logger.warning("Invalid marine data received for tomorrow forecast, using fallback")
                marine_data = weather_service._get_fallback_marine_data()
                
            if not standard_data or not standard_data.get('hourly'):
                logger.warning("Invalid standard data received for tomorrow forecast, using fallback")
                standard_data = weather_service._get_fallback_standard_data()
                
        except Exception as e:
            logger.error(f"Critical error fetching weather data for tomorrow forecast: {e}")
            return jsonify({
                "error": "Weather service unavailable", 
                "details": "Using fallback data",
                "fallback": True
            }), 503
        
        # Get timezone info
        tz_offset_sec = int(standard_data.get('utc_offset_seconds') or 0)
        local_now = datetime.utcnow() + timedelta(seconds=tz_offset_sec)
        tomorrow = (local_now + timedelta(days=1)).date()
        
        # Get hourly data
        hourly_standard = standard_data.get('hourly', {})
        hourly_marine = marine_data.get('hourly', {})
        
        times: List[str] = hourly_standard.get('time') or []
        
        # Filter for tomorrow's hours only (excluding night hours 22:00-04:00)
        tomorrow_indices = []
        tomorrow_times = []
        for i, time_str in enumerate(times):
            try:
                dt = dateparser.isoparse(time_str)
                if dt.date() == tomorrow:
                    hour = dt.hour
                    # Exclude night hours (22:00-04:00)
                    if not (hour >= 22 or hour <= 4):
                        tomorrow_indices.append(i)
                        tomorrow_times.append(time_str)
            except Exception:
                continue
        
        # Helper function to get hourly values
        def safe_get_hourly_tomorrow(data_dict, key: str, indices: List[int], default=0):
            values = data_dict.get(key, [])
            result = []
            for i in indices:
                if i < len(values) and values[i] is not None:
                    try:
                        result.append(float(values[i]))
                    except (ValueError, TypeError):
                        result.append(default)
                else:
                    result.append(default)
            return result
        
        # Get hourly values for tomorrow
        wind_speeds_ms = safe_get_hourly_tomorrow(hourly_standard, 'wind_speed_10m', tomorrow_indices, 0.0)
        wind_directions = safe_get_hourly_tomorrow(hourly_standard, 'wind_direction_10m', tomorrow_indices, 180)
        wind_gusts_ms = safe_get_hourly_tomorrow(hourly_standard, 'wind_gusts_10m', tomorrow_indices, 0.0)
        temperatures = safe_get_hourly_tomorrow(hourly_standard, 'temperature_2m', tomorrow_indices, 20.0)
        
        # Marine data for tomorrow
        marine_times = hourly_marine.get('time', [])
        marine_tomorrow_indices = []
        for i, time_str in enumerate(marine_times):
            try:
                dt = dateparser.isoparse(time_str)
                if dt.date() == tomorrow:
                    marine_tomorrow_indices.append(i)
            except Exception:
                continue
        
        wave_heights = safe_get_hourly_tomorrow(hourly_marine, 'wave_height', marine_tomorrow_indices, 0.5)
        wave_periods = safe_get_hourly_tomorrow(hourly_marine, 'wave_period', marine_tomorrow_indices, 5.0)
        
        # Create tomorrow's hourly forecast data
        tomorrow_forecast = []
        
        for i, time_str in enumerate(tomorrow_times):
            try:
                dt = dateparser.isoparse(time_str)
                hour_display = dt.strftime("%H:%M")
                
                # Get values for this hour
                wind_speed_ms = wind_speeds_ms[i] if i < len(wind_speeds_ms) else 0.0
                wind_speed_knots = wind_speed_ms * 1.944
                wind_dir = int(wind_directions[i]) if i < len(wind_directions) else 180
                wind_gust_ms = wind_gusts_ms[i] if i < len(wind_gusts_ms) else wind_speed_ms
                temp = temperatures[i] if i < len(temperatures) else 20.0
                
                # Marine data
                marine_index = min(i, len(wave_heights) - 1) if wave_heights else 0
                wave_height = wave_heights[marine_index] if wave_heights else 0.5
                wave_period = wave_periods[marine_index] if wave_periods else 5.0
                
                # Simple wingfoil analysis for this hour
                try:
                    # Get wingfoil preferences
                    prefs = config.get('wingfoil_preferences', {})
                    min_wind = prefs.get('min_wind_knots', 8)
                    max_wind = prefs.get('max_wind_knots', 35)
                    optimal_min = prefs.get('optimal_wind_min', 12)
                    optimal_max = prefs.get('optimal_wind_max', 22)
                    
                    # Simple wind scoring
                    if wind_speed_knots < min_wind:
                        wind_score = 0
                        wind_eval = f"Too light ({wind_speed_knots:.1f}kts)"
                    elif wind_speed_knots > max_wind:
                        wind_score = 15
                        wind_eval = f"Too strong ({wind_speed_knots:.1f}kts)"
                    elif optimal_min <= wind_speed_knots <= optimal_max:
                        wind_score = 100
                        wind_eval = f"Perfect ({wind_speed_knots:.1f}kts)"
                    else:
                        wind_score = 70
                        wind_eval = f"Acceptable ({wind_speed_knots:.1f}kts)"
                    # Gustiness penalty
                    try:
                        gust_knots_h2 = float(wind_gust_ms) * 1.944
                        base_knots_h2 = max(float(wind_speed_knots), 0.1)
                        gust_factor_h2 = (gust_knots_h2 / base_knots_h2) if base_knots_h2 > 0 else 1.0
                    except Exception:
                        gust_factor_h2 = 1.0
                    if gust_factor_h2 > 1.10:
                        if gust_factor_h2 <= 1.25:
                            wind_score -= 10
                            wind_eval += ", moderately gusty"
                        elif gust_factor_h2 <= 1.40:
                            wind_score -= 20
                            wind_eval += ", gusty"
                        elif gust_factor_h2 <= 1.60:
                            wind_score -= 30
                            wind_eval += ", very gusty"
                        else:
                            wind_score -= 40
                            wind_eval += ", extremely gusty"
                        wind_score = max(0, int(wind_score))
                    
                    # Simple wave scoring
                    if wave_height > 2.0:
                        wave_score = 30
                    elif wave_height < 0.2:
                        wave_score = 100
                    else:
                        wave_score = 85
                    
                    # Overall score
                    overall_score = int((wind_score * 0.8) + (wave_score * 0.2))
                    
                    # Overall conditions
                    if overall_score >= 85:
                        overall_conditions = "Excellent"
                    elif overall_score >= 70:
                        overall_conditions = "Good"
                    elif overall_score >= 60:
                        overall_conditions = "Marginal"
                    else:
                        overall_conditions = "Poor"
                    
                    # Weight-specific wing size recommendation
                    rider_weight = config.get('user', {}).get('rider_weight_kg', 80)
                    
                    # Base wing sizes for ~80kg rider
                    if wind_speed_knots < 8:
                        base_size = "7-8m"
                    elif wind_speed_knots < 12:
                        base_size = "6-7m"
                    elif wind_speed_knots < 16:
                        base_size = "5-6m"
                    elif wind_speed_knots < 20:
                        base_size = "4-5m"
                    elif wind_speed_knots < 25:
                        base_size = "3.5-4m"
                    else:
                        base_size = "3m"
                    
                    # Adjust for rider weight
                    if rider_weight >= 90:
                        if wind_speed_knots < 8:
                            wing_size = "8-9m"
                        elif wind_speed_knots < 12:
                            wing_size = "7-8m"
                        elif wind_speed_knots < 16:
                            wing_size = "6-7m"
                        elif wind_speed_knots < 20:
                            wing_size = "5-6m"
                        elif wind_speed_knots < 25:
                            wing_size = "4-5m"
                        else:
                            wing_size = "3.5-4m"
                    elif rider_weight <= 65:
                        if wind_speed_knots < 8:
                            wing_size = "6-7m"
                        elif wind_speed_knots < 12:
                            wing_size = "5-6m"
                        elif wind_speed_knots < 16:
                            wing_size = "4-5m"
                        elif wind_speed_knots < 20:
                            wing_size = "3.5-4m"
                        elif wind_speed_knots < 25:
                            wing_size = "3m"
                        else:
                            wing_size = "2.5-3m"
                    else:
                        wing_size = base_size
                    
                    wingfoil_data = {
                        "score": overall_score,
                        "suitable": overall_score >= 60,
                        "overall_conditions": overall_conditions,
                        "wind_evaluation": wind_eval,
                        "wing_size": wing_size
                    }
                except Exception as e:
                    logger.warning(f"Error analyzing wingfoil conditions for tomorrow hour {hour_display}: {e}")
                    wingfoil_data = {
                        "score": 0,
                        "suitable": False,
                        "overall_conditions": "Analysis Error",
                        "wind_evaluation": "N/A",
                        "wing_size": "N/A"
                    }
                
                # Create summary for this hour
                hour_summary = {
                    "time": hour_display,
                    "timestamp": time_str,
                    "wind": {
                        "speed_knots": round(wind_speed_knots, 1),
                        "direction": wind_dir,
                        "gust_knots": round(wind_gust_ms * 1.944, 1)
                    },
                    "waves": {
                        "height_m": round(wave_height, 1),
                        "period_s": round(wave_period, 1)
                    },
                    "conditions": {
                        "temperature": round(temp, 1)
                    },
                    "wingfoil": wingfoil_data
                }
                
                tomorrow_forecast.append(hour_summary)
                
            except Exception as e:
                logger.warning(f"Error processing tomorrow hour {i}: {e}")
                continue
        
        return jsonify({
            "date": str(tomorrow),
            "location": location['name'],
            "hourly_forecast": tomorrow_forecast,
            "summary": {
                "total_hours": len(tomorrow_forecast),
                "good_hours": len([h for h in tomorrow_forecast if h['wingfoil']['score'] >= 70]),
                "suitable_hours": len([h for h in tomorrow_forecast if h['wingfoil']['suitable']])
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting tomorrow forecast: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/daily-summary')
def get_daily_summary():
    """Daily summary for the current local day at the configured location"""
    try:
        config = load_config()
        location = config['location']
        std = weather_service.fetch_standard_weather(location['latitude'], location['longitude'])
        mar = weather_service.fetch_marine_weather(location['latitude'], location['longitude'])
        if not std or not mar:
            return jsonify({"error": "Failed to fetch weather data"}), 500

        tz_offset_sec = int(std.get('utc_offset_seconds') or 0)
        local_now = datetime.utcnow() + timedelta(seconds=tz_offset_sec)
        local_day = local_now.date()

        hourly = std.get('hourly', {})
        times: List[str] = hourly.get('time') or []
        idx_today: List[int] = []
        for i, t in enumerate(times):
            try:
                dt = dateparser.isoparse(t)
            except Exception:
                continue
            if (dt.date() == local_day):
                idx_today.append(i)

        def pick(arr_key: str, default: float = 0.0) -> List[float]:
            arr = hourly.get(arr_key) or []
            return [float(arr[i]) if i < len(arr) and arr[i] is not None else default for i in idx_today]

        wind_ms = pick('wind_speed_10m', 0.0)
        gust_ms = pick('wind_gusts_10m', 0.0)
        temp_c = pick('temperature_2m', 0.0)

        marine_h = mar.get('hourly', {}).get('wave_height') or []
        marine_t = mar.get('hourly', {}).get('time') or []
        marine_idx = [i for i, t in enumerate(marine_t) if (dateparser.isoparse(t).date() == local_day)]
        waves = [float(marine_h[i]) if i < len(marine_h) and marine_h[i] is not None else 0.0 for i in marine_idx]

        def stats(vals: List[float]) -> Dict[str, float]:
            if not vals:
                return {"min": 0, "max": 0, "avg": 0}
            return {
                "min": round(min(vals), 2),
                "max": round(max(vals), 2),
                "avg": round(sum(vals) / len(vals), 2)
            }

        # Convert to knots for wind
        wind_knots = [v * 1.944 for v in wind_ms]
        gust_knots = [v * 1.944 for v in gust_ms]

        prefs = config.get('wingfoil_preferences', {})
        opt_min = float(prefs.get('optimal_wind_min', 15))
        opt_max = float(prefs.get('optimal_wind_max', 25))
        # Find windows (indices) where wind within optimal range
        windows = []
        start = None
        for i, v in enumerate(wind_knots):
            if opt_min <= v <= opt_max:
                if start is None:
                    start = i
            else:
                if start is not None:
                    windows.append((start, i - 1))
                    start = None
        if start is not None:
            windows.append((start, len(wind_knots) - 1))

        def idx_to_time(i: int) -> str:
            if i < 0 or i >= len(idx_today):
                return ""
            src_i = idx_today[i]
            return times[src_i] if src_i < len(times) else ""

        pretty_windows = [{"from": idx_to_time(a), "to": idx_to_time(b)} for (a, b) in windows]

        summary = {
            "day": str(local_day),
            "wind_knots": stats(wind_knots),
            "gust_knots": stats(gust_knots),
            "temperature_c": stats(temp_c),
            "wave_height_m": stats(waves),
            "optimal_windows": pretty_windows
        }
        return jsonify(summary)
    except Exception as e:
        logger.error(f"Error building daily summary: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    """Get or update configuration"""
    if request.method == 'GET':
        return jsonify(_sanitize_config(load_config()))
    else:
        try:
            if not _require_admin(request):
                return jsonify({"error": "Unauthorized"}), 401
            incoming = request.get_json(force=True, silent=False) or {}
            if not isinstance(incoming, dict):
                return jsonify({"error": "Invalid config payload"}), 400
            config_path = '/app/config/config.json'
            current = load_config()
            # Merge shallowly
            merged = {**current, **incoming}
            with open(config_path, 'w') as f:
                json.dump(merged, f, indent=2)
            init_services()  # reload services with new config
            return jsonify({"message": "Config updated", "config": _sanitize_config(merged)})
        except Exception as e:
            logger.error(f"Error updating config: {e}")
            return jsonify({"error": str(e)}), 500

@app.route('/settings')
def settings_page():
    return render_template('settings.html')

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    })

if __name__ == '__main__':
    init_services()
    app.run(host='0.0.0.0', port=5000, debug=False)
