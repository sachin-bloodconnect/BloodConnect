CREATE DATABASE IF NOT EXISTS bloodconnect;
USE bloodconnect;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(120) NOT NULL,
    email VARCHAR(160) NOT NULL UNIQUE,
    mobile VARCHAR(20) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    city VARCHAR(100) NOT NULL,
    area VARCHAR(100) NOT NULL,
    blood_group ENUM('A+','A-','B+','B-','AB+','AB-','O+','O-') NOT NULL,
    role ENUM('USER','DONOR','HOSPITAL','ADMIN') NOT NULL DEFAULT 'USER',
    is_verified TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_users_city_area (city, area),
    INDEX idx_users_role (role)
);

CREATE TABLE IF NOT EXISTS donors (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    last_donation_date DATE NULL,
    availability ENUM('Available','Maybe Available','Not Available') NOT NULL DEFAULT 'Available',
    verified TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_donor_status (availability, verified)
);

CREATE TABLE IF NOT EXISTS hospitals (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    hospital_name VARCHAR(180) NOT NULL,
    registration_no VARCHAR(100) NOT NULL UNIQUE,
    address VARCHAR(255) NOT NULL,
    pincode VARCHAR(10),
    contact_person VARCHAR(120),
    verified TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_hospital_city (user_id)
);

CREATE TABLE IF NOT EXISTS blood_groups (
    id INT AUTO_INCREMENT PRIMARY KEY,
    group_name VARCHAR(5) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS blood_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    hospital_id INT NULL,
    blood_group VARCHAR(5) NOT NULL,
    quantity INT NOT NULL,
    required_date DATE NOT NULL,
    city VARCHAR(100) NOT NULL,
    area VARCHAR(100) NOT NULL,
    priority ENUM('Normal','Urgent','Critical') NOT NULL DEFAULT 'Normal',
    notes TEXT,
    status ENUM('Searching','Donor Found','Donor Responded','Hospital Confirmed','Completed','Cancelled') NOT NULL DEFAULT 'Searching',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE SET NULL,
    INDEX idx_request_search (blood_group, city, area, status)
);

CREATE TABLE IF NOT EXISTS emergency_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    request_id INT NOT NULL UNIQUE,
    emergency_reason VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES blood_requests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS request_responses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    request_id INT NOT NULL,
    donor_id INT NOT NULL,
    response ENUM('Accepted','Declined') NOT NULL,
    responded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_response (request_id, donor_id),
    FOREIGN KEY (request_id) REFERENCES blood_requests(id) ON DELETE CASCADE,
    FOREIGN KEY (donor_id) REFERENCES donors(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS donations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    donor_id INT NOT NULL,
    donation_date DATE NOT NULL,
    units INT NOT NULL DEFAULT 1,
    notes VARCHAR(255),
    FOREIGN KEY (donor_id) REFERENCES donors(id) ON DELETE CASCADE,
    INDEX idx_donation_donor (donor_id, donation_date)
);

CREATE TABLE IF NOT EXISTS blood_stock (
    id INT AUTO_INCREMENT PRIMARY KEY,
    hospital_id INT NOT NULL,
    blood_group VARCHAR(5) NOT NULL,
    quantity INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_hospital_group (hospital_id, blood_group),
    FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS blood_camps (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(180) NOT NULL,
    camp_date DATE NOT NULL,
    camp_time TIME NOT NULL,
    location VARCHAR(255) NOT NULL,
    organizer VARCHAR(150) NOT NULL,
    hospital_id INT NULL,
    registration_open TINYINT(1) NOT NULL DEFAULT 1,
    description TEXT,
    contact_information VARCHAR(180),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE SET NULL,
    INDEX idx_camp_date (camp_date)
);

CREATE TABLE IF NOT EXISTS camp_registrations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    camp_id INT NOT NULL,
    user_id INT NOT NULL,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_camp_user (camp_id, user_id),
    FOREIGN KEY (camp_id) REFERENCES blood_camps(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    title VARCHAR(180) NOT NULL,
    message TEXT NOT NULL,
    is_read TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_notification_user (user_id, is_read, created_at)
);

CREATE TABLE IF NOT EXISTS donor_badges (
    id INT AUTO_INCREMENT PRIMARY KEY,
    donor_id INT NOT NULL,
    badge_name VARCHAR(100) NOT NULL,
    awarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_donor_badge (donor_id, badge_name),
    FOREIGN KEY (donor_id) REFERENCES donors(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS admin_users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS password_resets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    token VARCHAR(120) NOT NULL UNIQUE,
    expires_at DATETIME NOT NULL,
    used TINYINT(1) NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_reset_token (token)
);

INSERT IGNORE INTO blood_groups (group_name) VALUES
('A+'),('A-'),('B+'),('B-'),('AB+'),('AB-'),('O+'),('O-');
