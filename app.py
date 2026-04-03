from flask import Flask, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime, timedelta

app = Flask(__name__)

# Configuration Cloud SQL
DB_HOST = os.getenv('DB_HOST', '104.155.118.7')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', '2s2Sr2AINWaGnp0yU7EcsjAw')
DB_NAME = os.getenv('DB_NAME', 'staffing-logs-db')
DB_PORT = os.getenv('DB_PORT', '5432')

def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            port=DB_PORT,
            sslmode='require'
        )
        return conn
    except Exception as e:
        print(f"DB Connection Error: {e}")
        return None

@app.route('/')
def index():
    with open('index.html', 'r') as f:
        return f.read()

@app.route('/api/metrics')
def metrics():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'DB Connection Failed'}), 500
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # ========== MÉTRIQUES GLOBALES ==========
        cur.execute("""
            SELECT 
                COUNT(*) as total_calls,
                SUM(total_tokens) as total_tokens,
                SUM(cost_usd) as total_cost,
                AVG(latency_ms) as avg_latency,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_calls,
                SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) as failed_calls
            FROM llm_telemetry
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        metrics_data = cur.fetchone()
        
        # ========== GOLDEN SIGNAL #1 : LATENCY ==========
        cur.execute("""
            SELECT actor_name, model, 
                   ROUND(AVG(latency_ms)::numeric, 2) as avg_latency_ms,
                   ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)::numeric, 2) as p95_latency_ms,
                   COUNT(*) as calls
            FROM llm_telemetry
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY actor_name, model
            ORDER BY avg_latency_ms DESC
        """)
        latency_data = [dict(row) for row in cur.fetchall()]
        
        # ========== GOLDEN SIGNAL #2 : TRAFFIC ==========
        cur.execute("""
            SELECT actor_name, model,
                   COUNT(*) as total_calls,
                   SUM(total_tokens) as total_tokens,
                   ROUND(AVG(total_tokens)::numeric, 2) as avg_tokens_per_call
            FROM llm_telemetry
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY actor_name, model
            ORDER BY total_calls DESC
        """)
        traffic_data = [dict(row) for row in cur.fetchall()]
        
        # ========== GOLDEN SIGNAL #3 : ERRORS ==========
        cur.execute("""
            SELECT actor_name, model, status,
                   COUNT(*) as error_count,
                   ROUND((COUNT(*) * 100.0 / (SELECT COUNT(*) FROM llm_telemetry WHERE created_at >= NOW() - INTERVAL '24 hours'))::numeric, 2) as error_rate_percent
            FROM llm_telemetry
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY actor_name, model, status
            ORDER BY error_count DESC
        """)
        errors_data = [dict(row) for row in cur.fetchall()]
        
        # ========== GOLDEN SIGNAL #4 : SATURATION ==========
        cur.execute("""
            SELECT actor_name, model,
                   COUNT(*) as total_calls,
                   ROUND(AVG(queue_depth)::numeric, 2) as avg_queue_depth,
                   ROUND(AVG(retry_count)::numeric, 2) as avg_retry_count,
                   ROUND((SUM(CASE WHEN retry_count > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*))::numeric, 2) as retry_rate_percent
            FROM llm_telemetry
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY actor_name, model
            ORDER BY avg_queue_depth DESC
        """)
        saturation_data = [dict(row) for row in cur.fetchall()]
        
        # ========== COÛT PAR AGENT ==========
        cur.execute("""
            SELECT actor_name, 
                   COUNT(*) as calls,
                   SUM(cost_usd) as cost_usd,
                   SUM(total_tokens) as total_tokens
            FROM llm_telemetry
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY actor_name
            ORDER BY cost_usd DESC
        """)
        cost_by_agent = [dict(row) for row in cur.fetchall()]
        
        # ========== COÛT PAR MODÈLE ==========
        cur.execute("""
            SELECT model,
                   COUNT(*) as calls,
                   SUM(cost_usd) as cost_usd,
                   SUM(total_tokens) as total_tokens
            FROM llm_telemetry
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY model
            ORDER BY cost_usd DESC
        """)
        cost_by_model = [dict(row) for row in cur.fetchall()]
        
        # ========== TIMELINE (24h) ==========
        cur.execute("""
            SELECT DATE_TRUNC('hour', created_at) as hour,
                   SUM(cost_usd) as cost_usd,
                   COUNT(*) as calls,
                   SUM(total_tokens) as total_tokens
            FROM llm_telemetry
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY DATE_TRUNC('hour', created_at)
            ORDER BY hour ASC
        """)
        timeline = [{'hour': str(row['hour']), 'cost_usd': float(row['cost_usd'] or 0), 'calls': int(row['calls']), 'tokens': int(row['total_tokens'] or 0)} for row in cur.fetchall()]
        
        # ========== LOGS RÉCENTS ==========
        cur.execute("""
            SELECT actor_name, model, total_tokens, latency_ms, cost_usd, status, created_at
            FROM llm_telemetry
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            ORDER BY created_at DESC
            LIMIT 100
        """)
        logs = [dict(row) for row in cur.fetchall()]
        
        cur.close()
        conn.close()
        
        return jsonify({
            'metrics': {
                'total_cost': float(metrics_data['total_cost'] or 0),
                'total_tokens': int(metrics_data['total_tokens'] or 0),
                'total_calls': int(metrics_data['total_calls'] or 0),
                'avg_latency': float(metrics_data['avg_latency'] or 0),
                'successful_calls': int(metrics_data['successful_calls'] or 0),
                'failed_calls': int(metrics_data['failed_calls'] or 0),
                'error_rate': round((int(metrics_data['failed_calls'] or 0) * 100.0 / max(int(metrics_data['total_calls'] or 1), 1)), 2)
            },
            'golden_signals': {
                'latency': latency_data,
                'traffic': traffic_data,
                'errors': errors_data,
                'saturation': saturation_data
            },
            'cost_breakdown': {
                'by_agent': cost_by_agent,
                'by_model': cost_by_model
            },
            'timeline': timeline,
            'logs': logs
        })
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/health')
def health():
    return jsonify({'status': 'healthy'})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
