import logging

logger = logging.getLogger(__name__)

class WeightCalculator:
    """
    Application service to determine weight factors based on broadcast time.
    """
    def __init__(self) -> None:
        pass

    def get_weight(self, time_str: str) -> float:
        """
        Parses time (HH:mm) and returns corresponding weight factor.
        - 09:00~11:59, 20:00~22:59 -> 1.5 (Prime)
        - 13:00~16:59 -> 1.2 (Active)
        - 01:00~05:59 -> 0.7 (Late Night)
        - Others -> 1.0 (Standard)
        """
        if (not time_str):
            return 1.0
            
        try:
            # Extract hour as integer
            hour_part = time_str.split(":")[0]
            hour = int(hour_part)
            
            weight = 1.0
            
            # Use explicit if-elif-else as requested
            if ( (hour >= 9 and hour <= 11) or (hour >= 20 and hour <= 22) ):
                weight = 1.5
            elif ( (hour >= 13 and hour <= 16) ):
                weight = 1.2
            elif ( (hour >= 1 and hour <= 5) ):
                weight = 0.7
            else:
                weight = 1.0
                
            return weight

        except Exception as e:
            logger.error(f"Error parsing time for weight calculation: {str(e)}")
            return 1.0
