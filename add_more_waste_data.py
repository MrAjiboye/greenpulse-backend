from datetime import datetime, timedelta
import random
from app.database import SessionLocal
from app.models import WasteLog

def add_waste_data():
    db = SessionLocal()
    
    try:
        # Delete existing waste logs
        db.query(WasteLog).delete()
        db.commit()
        
        print("Creating waste logs...")
        streams = ["Recycling", "Compost", "Landfill"]
        locations = ["Main Kitchen", "Loading Dock A", "Cafeteria", "Production Area"]
        
        # Create 90 logs over 30 days
        for days_ago in range(30):
            for _ in range(3):  # 3 logs per day
                timestamp = datetime.utcnow() - timedelta(days=days_ago, hours=random.randint(8, 18))
                stream = random.choice(streams)
                
                # Different weight ranges per stream
                if stream == "Recycling":
                    weight = random.uniform(150, 250)
                elif stream == "Compost":
                    weight = random.uniform(80, 150)
                else:  # Landfill
                    weight = random.uniform(100, 180)
                
                log = WasteLog(
                    timestamp=timestamp,
                    stream=stream,
                    weight_kg=round(weight, 2),
                    location=random.choice(locations),
                    contamination_detected=random.random() < 0.05,
                    facility_id=1
                )
                db.add(log)
        
        db.commit()
        
        # Verify
        count = db.query(WasteLog).count()
        print(f"✅ Created {count} waste logs")
        
        # Show breakdown
        from sqlalchemy import func
        breakdown = db.query(
            WasteLog.stream,
            func.sum(WasteLog.weight_kg).label('total')
        ).group_by(WasteLog.stream).all()
        
        print("\n📊 Waste Breakdown:")
        for stream, total in breakdown:
            print(f"  {stream}: {total:.1f} kg")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    add_waste_data()