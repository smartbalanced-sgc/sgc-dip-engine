"""
Configuration Loader — SGC Dip Engine v7
Loads and validates config.yaml, provides clean interface for all modules.
"""

import os
import sys
from pathlib import Path
import yaml


class ConfigLoader:
    """Singleton config loader with validation."""
    
    _instance = None
    _config = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def load(self, config_path=None):
        """Load config.yaml and validate required fields."""
        if self._config is not None:
            return self._config
        
        # Find config.yaml
        if config_path is None:
            # Try multiple locations
            repo_root = Path(__file__).parent.parent
            candidates = [
                repo_root / 'config' / 'config.yaml',
                repo_root / 'config.yaml',
                Path('config/config.yaml'),
                Path('config.yaml'),
            ]
            
            for path in candidates:
                if path.exists():
                    config_path = path
                    break
            
            if config_path is None:
                raise FileNotFoundError(
                    "config.yaml not found. Searched: " + 
                    ", ".join(str(p) for p in candidates)
                )
        
        # Load YAML
        with open(config_path, 'r') as f:
            self._config = yaml.safe_load(f)
        
        # Validate required sections
        self._validate()
        
        print(f"✅ Loaded config from: {config_path}")
        return self._config
    
    def _validate(self):
        """Validate required config sections and fields."""
        required = {
            'portfolio': ['tickers'],
            'signal': ['percentile_target', 'min_actionable_dip_pct'],
            'monte_carlo': ['num_paths', 'simulation_days'],
        }
        
        for section, fields in required.items():
            if section not in self._config:
                raise ValueError(f"Missing required section: {section}")
            
            for field in fields:
                if field not in self._config[section]:
                    raise ValueError(f"Missing required field: {section}.{field}")
        
        # Validate types and ranges
        self._validate_types()
    
    def _validate_types(self):
        """Validate parameter types and ranges."""
        cfg = self._config
        
        # Percentile must be 0-100
        pct = cfg['signal']['percentile_target']
        if not (0 <= pct <= 100):
            raise ValueError(f"percentile_target must be 0-100, got {pct}")
        
        # Min dip must be 0-1 (0-100%)
        min_dip = cfg['signal']['min_actionable_dip_pct']
        if not (0 <= min_dip <= 1):
            raise ValueError(f"min_actionable_dip_pct must be 0-1, got {min_dip}")
        
        # Num paths must be positive
        paths = cfg['monte_carlo']['num_paths']
        if paths <= 0:
            raise ValueError(f"num_paths must be positive, got {paths}")
        
        # Simulation days must be positive
        days = cfg['monte_carlo']['simulation_days']
        if days <= 0:
            raise ValueError(f"simulation_days must be positive, got {days}")
    
    def get(self, *keys, default=None):
        """Get nested config value with dot notation.
        
        Examples:
            config.get('signal', 'percentile_target')  # Returns 60
            config.get('portfolio', 'tickers')  # Returns list
        """
        if self._config is None:
            self.load()
        
        value = self._config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value
    
    def __getitem__(self, key):
        """Dict-style access: config['signal']"""
        if self._config is None:
            self.load()
        return self._config[key]


# Singleton instance
_loader = ConfigLoader()


def load_config(config_path=None):
    """Load and return config dict.
    
    Args:
        config_path: Optional path to config.yaml. If None, searches standard locations.
    
    Returns:
        dict: Loaded configuration
    """
    return _loader.load(config_path)


def get_config(*keys, default=None):
    """Get nested config value.
    
    Examples:
        get_config('signal', 'percentile_target')  # Returns 60
        get_config('portfolio', 'tickers')  # Returns list
    """
    return _loader.get(*keys, default=default)


def reload_config():
    """Force reload config (for testing)."""
    _loader._config = None
    return _loader.load()


# Load on import for convenience
try:
    load_config()
except FileNotFoundError as e:
    print(f"⚠️  Config not loaded: {e}", file=sys.stderr)
    print("   Config will be loaded on first access.", file=sys.stderr)
