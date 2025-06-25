-- create_tables.sql
CREATE TABLE IF NOT EXISTS chatmessages (
    id SERIAL PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);