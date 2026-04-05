#!/bin/bash
# Davidsino Rewards - Quick Start Script
# Run this after installing dependencies

set -e

echo "🎰 Davidsino Rewards Setup"
echo "=========================="

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Install with: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate and install deps
source venv/bin/activate
pip install -q -r requirements.txt

# Copy .env if it doesn't exist
if [ ! -f ".env" ]; then
    echo "📝 Creating .env from example..."
    cp .env.example .env
    echo "⚠️  Edit .env and set your DATABASE_URL and ADMIN_PIN"
fi

echo ""
echo "✅ Dependencies installed"
echo ""
echo "📋 Next steps:"
echo "1. Make sure PostgreSQL is running: sudo systemctl start postgresql"
echo "2. Run: createdb -U postgres davidsino"
echo "3. Run: createuser -U postgres -P davidsino  (password: davidsino_pass)"
echo "4. Run: psql -U postgres -c 'ALTER USER davidsino CREATEDB;'"
echo "5. Edit .env if needed"
echo "6. Start server: python3 main.py"
echo ""
echo "🌐 Then visit http://YOUR_IP:8000 from your phone"
