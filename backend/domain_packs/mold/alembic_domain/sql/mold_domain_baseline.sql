-- Frozen PostgreSQL DDL for MoldPilot domain baseline m10d0e000001.

-- table: customer

CREATE TABLE customer (
	code VARCHAR(80) NOT NULL,
	name VARCHAR(150) NOT NULL,
	rule_key VARCHAR(80) NOT NULL,
	active BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (code)
);

-- table: material

CREATE TABLE material (
	code VARCHAR(80) NOT NULL,
	name VARCHAR(150) NOT NULL,
	category VARCHAR(60) NOT NULL,
	unit VARCHAR(20) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (code)
);

-- table: mold

CREATE TABLE mold (
	internal_number VARCHAR(80) NOT NULL,
	name VARCHAR(150) NOT NULL,
	status VARCHAR(30) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (internal_number)
);

-- table: project

CREATE TABLE project (
	code VARCHAR(80) NOT NULL,
	name VARCHAR(150) NOT NULL,
	status VARCHAR(30) NOT NULL,
	row_version INTEGER NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (code)
);

-- table: supplier

CREATE TABLE supplier (
	code VARCHAR(80) NOT NULL,
	name VARCHAR(150) NOT NULL,
	category VARCHAR(60) NOT NULL,
	active BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (code)
);

-- table: warehouse

CREATE TABLE warehouse (
	code VARCHAR(80) NOT NULL,
	name VARCHAR(150) NOT NULL,
	active BOOLEAN NOT NULL,
	scope_confirmed BOOLEAN NOT NULL,
	start_date DATE,
	opening_evidence TEXT,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (code)
);

-- table: business_subject

CREATE TABLE business_subject (
	kind VARCHAR(60) NOT NULL,
	number VARCHAR(80) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	category VARCHAR(60),
	warehouse_id VARCHAR(36),
	created_by VARCHAR(36) NOT NULL,
	status VARCHAR(30) NOT NULL,
	revision INTEGER NOT NULL,
	round_no INTEGER NOT NULL,
	remark TEXT NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT subject_status CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','RETURNED','APPLY_BLOCKED','EFFECTIVE','CANCELLED','CLOSED')),
	UNIQUE (number),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(warehouse_id) REFERENCES warehouse (id),
	FOREIGN KEY(created_by) REFERENCES app_user (id)
);

-- table: contact_case

CREATE TABLE contact_case (
	project_id VARCHAR(36) NOT NULL,
	category VARCHAR(60),
	title VARCHAR(150) NOT NULL,
	description TEXT NOT NULL,
	mode VARCHAR(20) NOT NULL,
	created_by VARCHAR(36) NOT NULL,
	request_key VARCHAR(36) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	revision INTEGER NOT NULL,
	closed_at TIMESTAMP WITH TIME ZONE,
	closed_by VARCHAR(36),
	reviewer_id VARCHAR(36),
	customer_ref VARCHAR(200),
	customer_name VARCHAR(200),
	mold_number VARCHAR(100),
	product_ref VARCHAR(200),
	application_date DATE,
	problem_source VARCHAR(40),
	current_stage VARCHAR(200),
	change_type VARCHAR(30),
	urgency VARCHAR(20),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (created_by, request_key),
	CHECK (mode IN ('HISTORY','ONLINE')),
	CONSTRAINT contact_problem_source CHECK (problem_source IS NULL OR problem_source IN ('CUSTOMER_CHANGE','DESIGN_ISSUE','ASSEMBLY_ISSUE','MACHINING_ISSUE','PROCUREMENT_ISSUE','QUALITY_ISSUE','TRIAL_ISSUE','OUTSOURCE_DEFECT','COST_REDUCTION','PROCESS_IMPROVEMENT','OTHER')),
	CONSTRAINT contact_change_type CHECK (change_type IS NULL OR change_type IN ('CHANGE','EXCEPTION','IMPROVEMENT')),
	CONSTRAINT contact_urgency CHECK (urgency IS NULL OR urgency IN ('NORMAL','URGENT','CRITICAL')),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(created_by) REFERENCES app_user (id),
	FOREIGN KEY(closed_by) REFERENCES app_user (id),
	FOREIGN KEY(reviewer_id) REFERENCES app_user (id)
);

-- table: erp_identity

CREATE TABLE erp_identity (
	user_id VARCHAR(36) NOT NULL,
	erp_user_id VARCHAR(40) NOT NULL,
	token_ciphertext TEXT,
	authenticated_at TIMESTAMP WITH TIME ZONE,
	version INTEGER NOT NULL,
	PRIMARY KEY (user_id),
	FOREIGN KEY(user_id) REFERENCES app_user (id),
	UNIQUE (erp_user_id)
);

-- table: logistics_route

CREATE TABLE logistics_route (
	project_id VARCHAR(36),
	route_code VARCHAR(80) NOT NULL,
	origin VARCHAR(200) NOT NULL,
	destination VARCHAR(200) NOT NULL,
	carrier_name VARCHAR(150) NOT NULL,
	vehicle_type VARCHAR(80) NOT NULL,
	weight_kg NUMERIC(18, 3),
	transport_mode VARCHAR(40) NOT NULL,
	price_unit VARCHAR(40) NOT NULL,
	tax_mode VARCHAR(30) NOT NULL,
	valid_from DATE,
	valid_to DATE,
	active BOOLEAN NOT NULL,
	evidence TEXT NOT NULL,
	source_ref VARCHAR(120),
	confirmed_by VARCHAR(36),
	confirmed_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT logistics_route_transport_mode CHECK (transport_mode IN ('TRUCK','EXPRESS','SEA','AIR','RAIL','OTHER')),
	CONSTRAINT logistics_route_tax_mode CHECK (tax_mode IN ('TAX_INCLUDED','TAX_EXCLUDED','UNKNOWN')),
	CONSTRAINT logistics_route_positive_weight CHECK (weight_kg IS NULL OR weight_kg > 0),
	CONSTRAINT logistics_route_valid_range CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from),
	CONSTRAINT logistics_route_unique_source UNIQUE (source_ref),
	FOREIGN KEY(project_id) REFERENCES project (id),
	UNIQUE (route_code),
	FOREIGN KEY(confirmed_by) REFERENCES app_user (id)
);

-- table: project_mold

CREATE TABLE project_mold (
	project_id VARCHAR(36) NOT NULL,
	mold_id VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (project_id, mold_id),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(mold_id) REFERENCES mold (id)
);

-- table: project_profile

CREATE TABLE project_profile (
	project_id VARCHAR(36) NOT NULL,
	customer_id VARCHAR(36),
	owner_user_id VARCHAR(36) NOT NULL,
	execution_mode VARCHAR(30) NOT NULL,
	customer_due_date DATE,
	settlement_status VARCHAR(30) NOT NULL,
	PRIMARY KEY (project_id),
	CHECK (execution_mode IN ('INTERNAL','FULL_OUTSOURCE')),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(customer_id) REFERENCES customer (id),
	FOREIGN KEY(owner_user_id) REFERENCES app_user (id)
);

-- table: project_role_config

CREATE TABLE project_role_config (
	project_id VARCHAR(36) NOT NULL,
	version INTEGER NOT NULL,
	PRIMARY KEY (project_id),
	FOREIGN KEY(project_id) REFERENCES project (id)
);

-- table: project_role_member

CREATE TABLE project_role_member (
	project_id VARCHAR(36) NOT NULL,
	role_key VARCHAR(60) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (project_id, role_key, user_id),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(user_id) REFERENCES app_user (id)
);

-- table: purchase_request

CREATE TABLE purchase_request (
	number VARCHAR(80) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	created_by VARCHAR(36) NOT NULL,
	remark TEXT NOT NULL,
	status VARCHAR(30) NOT NULL,
	revision INTEGER NOT NULL,
	round_no INTEGER NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','RETURNED')),
	UNIQUE (number),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(created_by) REFERENCES app_user (id)
);

-- table: risk_analysis_result

CREATE TABLE risk_analysis_result (
	user_id VARCHAR(36) NOT NULL,
	authorization_hash VARCHAR(64) NOT NULL,
	policy_version INTEGER NOT NULL,
	trigger_type VARCHAR(20) NOT NULL,
	findings JSONB NOT NULL,
	limitations JSONB NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES app_user (id)
);

-- table: risk_policy

CREATE TABLE risk_policy (
	version INTEGER NOT NULL,
	near_due_days INTEGER NOT NULL,
	configured_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (near_due_days BETWEEN 0 AND 90),
	UNIQUE (version),
	FOREIGN KEY(configured_by) REFERENCES app_user (id)
);

-- table: stock_balance

CREATE TABLE stock_balance (
	warehouse_id VARCHAR(36) NOT NULL,
	material_id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	quantity NUMERIC(18, 6) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (warehouse_id, material_id, project_id),
	CHECK (quantity >= 0),
	FOREIGN KEY(warehouse_id) REFERENCES warehouse (id),
	FOREIGN KEY(material_id) REFERENCES material (id),
	FOREIGN KEY(project_id) REFERENCES project (id)
);

-- table: assembly_detail

CREATE TABLE assembly_detail (
	subject_id VARCHAR(36) NOT NULL,
	design_id VARCHAR(36) NOT NULL,
	supervisor_id VARCHAR(36) NOT NULL,
	prerequisites_evidence TEXT NOT NULL,
	planned_date DATE NOT NULL,
	execution_status VARCHAR(30) NOT NULL,
	PRIMARY KEY (subject_id),
	CHECK (execution_status IN ('NOT_STARTED','RUNNING','DONE')),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(design_id) REFERENCES business_subject (id),
	FOREIGN KEY(supervisor_id) REFERENCES app_user (id)
);

-- table: assembly_execution

CREATE TABLE assembly_execution (
	assembly_id VARCHAR(36) NOT NULL,
	action VARCHAR(20) NOT NULL,
	actual_date DATE NOT NULL,
	evidence TEXT NOT NULL,
	confirmed_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (assembly_id, action),
	CHECK (action IN ('START','DONE')),
	FOREIGN KEY(assembly_id) REFERENCES business_subject (id),
	FOREIGN KEY(confirmed_by) REFERENCES app_user (id)
);

-- table: business_decision_detail

CREATE TABLE business_decision_detail (
	subject_id VARCHAR(36) NOT NULL,
	source_subject_id VARCHAR(36),
	decision VARCHAR(40) NOT NULL,
	execution_mode VARCHAR(30),
	effective_date DATE NOT NULL,
	evidence TEXT NOT NULL,
	amount NUMERIC(18, 2),
	currency VARCHAR(3),
	PRIMARY KEY (subject_id),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(source_subject_id) REFERENCES business_subject (id)
);

-- table: contact_record

CREATE TABLE contact_record (
	case_id VARCHAR(36) NOT NULL,
	author_id VARCHAR(36) NOT NULL,
	request_key VARCHAR(36) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	kind VARCHAR(30) NOT NULL,
	occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
	detail JSONB NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (case_id, author_id, request_key),
	FOREIGN KEY(case_id) REFERENCES contact_case (id),
	FOREIGN KEY(author_id) REFERENCES app_user (id)
);

-- table: contact_resolution

CREATE TABLE contact_resolution (
	subject_id VARCHAR(36) NOT NULL,
	case_id VARCHAR(36) NOT NULL,
	case_revision INTEGER NOT NULL,
	solution TEXT NOT NULL,
	customer_evidence TEXT,
	customer_due_affected BOOLEAN NOT NULL,
	material_snapshot JSONB NOT NULL,
	PRIMARY KEY (subject_id),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(case_id) REFERENCES contact_case (id)
);

-- table: contact_task

CREATE TABLE contact_task (
	case_id VARCHAR(36) NOT NULL,
	department_id VARCHAR(36) NOT NULL,
	title VARCHAR(150) NOT NULL,
	created_by VARCHAR(36) NOT NULL,
	assignee_id VARCHAR(36),
	status VARCHAR(30) NOT NULL,
	response TEXT,
	verified_plan_id VARCHAR(36),
	affected_type VARCHAR(40) NOT NULL,
	affected_ref VARCHAR(300) NOT NULL,
	impact_description TEXT NOT NULL,
	planned_action VARCHAR(30) NOT NULL,
	delivery_impact_days INTEGER NOT NULL,
	estimated_amount NUMERIC(18, 2),
	currency VARCHAR(3),
	source_system VARCHAR(20) NOT NULL,
	source_ref VARCHAR(300),
	source_as_of TIMESTAMP WITH TIME ZONE,
	actual_completed_at TIMESTAMP WITH TIME ZONE,
	actual_hours NUMERIC(12, 2),
	actual_amount NUMERIC(18, 2),
	actual_currency VARCHAR(3),
	execution_evidence TEXT,
	execution_source_system VARCHAR(20),
	execution_source_ref VARCHAR(300),
	execution_source_as_of TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT contact_task_state CHECK (status IN ('UNASSIGNED','ASSIGNED','RESPONDED','VERIFIED','CANCELLED')),
	CONSTRAINT contact_task_affected_type CHECK (affected_type IN ('DRAWING','MATERIAL','PURCHASE_ORDER','WIP_TASK','SUPPLIER_TASK','PLAN_NODE','CONTRACT','FINANCE','LOGISTICS','OTHER')),
	CONSTRAINT contact_task_planned_action CHECK (planned_action IN ('CONTINUE','PAUSE','CANCEL','REWORK','REISSUE')),
	CONSTRAINT contact_task_source CHECK (source_system IN ('AGENT','ERP','MANUAL')),
	CONSTRAINT contact_task_execution_source CHECK (execution_source_system IS NULL OR execution_source_system IN ('AGENT','ERP','MANUAL')),
	CONSTRAINT contact_task_delivery_days CHECK (delivery_impact_days >= 0),
	CONSTRAINT contact_task_estimated_amount CHECK (estimated_amount IS NULL OR estimated_amount >= 0),
	CONSTRAINT contact_task_actual_hours CHECK (actual_hours IS NULL OR actual_hours >= 0),
	CONSTRAINT contact_task_actual_amount CHECK (actual_amount IS NULL OR actual_amount >= 0),
	FOREIGN KEY(case_id) REFERENCES contact_case (id),
	FOREIGN KEY(department_id) REFERENCES assignment_group (id),
	FOREIGN KEY(created_by) REFERENCES app_user (id),
	FOREIGN KEY(assignee_id) REFERENCES app_user (id),
	FOREIGN KEY(verified_plan_id) REFERENCES business_subject (id)
);

-- table: contract_detail

CREATE TABLE contract_detail (
	subject_id VARCHAR(36) NOT NULL,
	customer_id VARCHAR(36),
	supplier_id VARCHAR(36),
	amount NUMERIC(18, 2) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	contract_number VARCHAR(100) NOT NULL,
	expected_date DATE,
	replaces_id VARCHAR(36),
	PRIMARY KEY (subject_id),
	CHECK (amount > 0),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(customer_id) REFERENCES customer (id),
	FOREIGN KEY(supplier_id) REFERENCES supplier (id),
	FOREIGN KEY(replaces_id) REFERENCES business_subject (id)
);

-- table: customer_delivery_signature

CREATE TABLE customer_delivery_signature (
	project_id VARCHAR(36) NOT NULL,
	logistics_route_id VARCHAR(36),
	shipment_reference VARCHAR(120) NOT NULL,
	signed_date DATE NOT NULL,
	signer_name VARCHAR(120) NOT NULL,
	sign_status VARCHAR(30) NOT NULL,
	move_type VARCHAR(40) NOT NULL,
	evidence TEXT NOT NULL,
	recorded_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT customer_delivery_signature_unique_ref_date UNIQUE (project_id, shipment_reference, signed_date),
	CONSTRAINT customer_delivery_signature_status CHECK (sign_status IN ('SIGNED','REJECTED','PENDING')),
	CONSTRAINT customer_delivery_signature_move_type CHECK (move_type IN ('DELIVERY','MOLD_TRANSFER','RETURN','OTHER')),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(logistics_route_id) REFERENCES logistics_route (id),
	FOREIGN KEY(recorded_by) REFERENCES app_user (id)
);

-- table: design_detail

CREATE TABLE design_detail (
	subject_id VARCHAR(36) NOT NULL,
	design_type VARCHAR(30),
	drawing_revision VARCHAR(100) NOT NULL,
	drawing_evidence TEXT NOT NULL,
	reviewer_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (subject_id),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(reviewer_id) REFERENCES app_user (id)
);

-- table: engineering_change_detail

CREATE TABLE engineering_change_detail (
	subject_id VARCHAR(36) NOT NULL,
	problem TEXT NOT NULL,
	solution TEXT NOT NULL,
	customer_due_affected BOOLEAN NOT NULL,
	customer_evidence TEXT,
	PRIMARY KEY (subject_id),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id)
);

-- table: erp_operation

CREATE TABLE erp_operation (
	user_id VARCHAR(36) NOT NULL,
	intent_id VARCHAR(36) NOT NULL,
	action VARCHAR(80) NOT NULL,
	native_id VARCHAR(80) NOT NULL,
	state VARCHAR(30) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	erp_user_id VARCHAR(40) NOT NULL,
	response JSONB,
	error_code VARCHAR(80),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (state IN ('DISPATCHING','SUCCEEDED','REJECTED','UNKNOWN','OBSERVED_APPLIED')),
	FOREIGN KEY(user_id) REFERENCES app_user (id),
	UNIQUE (intent_id),
	FOREIGN KEY(intent_id) REFERENCES human_action_intent (id)
);

-- table: logistics_quote

CREATE TABLE logistics_quote (
	route_id VARCHAR(36) NOT NULL,
	supplier_id VARCHAR(36),
	unit_price NUMERIC(18, 2) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	valid_from DATE NOT NULL,
	valid_to DATE NOT NULL,
	status VARCHAR(30) NOT NULL,
	settlement_for_project_id VARCHAR(36),
	quote_evidence TEXT NOT NULL,
	approved_by VARCHAR(36),
	approved_at TIMESTAMP WITH TIME ZONE,
	pricing_method VARCHAR(30) NOT NULL,
	comparison_count INTEGER NOT NULL,
	comparison_summary TEXT,
	reconciliation_basis TEXT,
	source_ref VARCHAR(120),
	created_by VARCHAR(36),
	supersedes_quote_id VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT logistics_quote_nonnegative_price CHECK (unit_price >= 0),
	CONSTRAINT logistics_quote_valid_range CHECK (valid_to >= valid_from),
	CONSTRAINT logistics_quote_status CHECK (status IN ('DRAFT','SUBMITTED','EFFECTIVE','EXPIRED','CANCELLED')),
	CONSTRAINT logistics_quote_pricing_method CHECK (pricing_method IN ('LEGACY','FIXED_ROUTE','COMPETITIVE','NEGOTIATED','SINGLE_SOURCE')),
	CONSTRAINT logistics_quote_comparison_count_nonnegative CHECK (comparison_count >= 0),
	CONSTRAINT logistics_quote_unique_source UNIQUE (route_id, source_ref),
	FOREIGN KEY(route_id) REFERENCES logistics_route (id),
	FOREIGN KEY(supplier_id) REFERENCES supplier (id),
	FOREIGN KEY(settlement_for_project_id) REFERENCES project (id),
	FOREIGN KEY(approved_by) REFERENCES app_user (id),
	FOREIGN KEY(created_by) REFERENCES app_user (id),
	FOREIGN KEY(supersedes_quote_id) REFERENCES logistics_quote (id)
);

-- table: pause_record

CREATE TABLE pause_record (
	project_id VARCHAR(36) NOT NULL,
	start_date DATE NOT NULL,
	end_date DATE,
	subject_id VARCHAR(36) NOT NULL,
	resume_subject_id VARCHAR(36),
	shifted_days INTEGER NOT NULL,
	shift_applied BOOLEAN NOT NULL,
	customer_due_date_snapshot DATE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id),
	UNIQUE (subject_id),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	UNIQUE (resume_subject_id),
	FOREIGN KEY(resume_subject_id) REFERENCES business_subject (id)
);

-- table: payment_confirmation

CREATE TABLE payment_confirmation (
	request_id VARCHAR(36) NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	paid_date DATE NOT NULL,
	reference VARCHAR(100) NOT NULL,
	evidence TEXT NOT NULL,
	confirmed_by VARCHAR(36) NOT NULL,
	reversal_of_id VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (amount <> 0),
	FOREIGN KEY(request_id) REFERENCES business_subject (id),
	UNIQUE (reference),
	FOREIGN KEY(confirmed_by) REFERENCES app_user (id),
	UNIQUE (reversal_of_id),
	FOREIGN KEY(reversal_of_id) REFERENCES payment_confirmation (id)
);

-- table: payment_stage

CREATE TABLE payment_stage (
	contract_id VARCHAR(36) NOT NULL,
	name VARCHAR(100) NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	condition TEXT NOT NULL,
	condition_confirmed BOOLEAN NOT NULL,
	condition_evidence TEXT,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (amount > 0),
	FOREIGN KEY(contract_id) REFERENCES business_subject (id)
);

-- table: plan_department_confirmation

CREATE TABLE plan_department_confirmation (
	plan_change_id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	department VARCHAR(100) NOT NULL,
	assigned_user_ids JSONB NOT NULL,
	task_keys JSONB NOT NULL,
	change_types JSONB NOT NULL,
	status VARCHAR(30) NOT NULL,
	version INTEGER NOT NULL,
	confirmed_by VARCHAR(36),
	confirmed_at TIMESTAMP WITH TIME ZONE,
	note TEXT,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (plan_change_id, department),
	CONSTRAINT plan_department_confirmation_status CHECK (status IN ('PENDING','CONFIRMED')),
	FOREIGN KEY(plan_change_id) REFERENCES business_subject (id),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(confirmed_by) REFERENCES app_user (id)
);

-- table: plan_detail

CREATE TABLE plan_detail (
	subject_id VARCHAR(36) NOT NULL,
	previous_id VARCHAR(36),
	reason TEXT NOT NULL,
	PRIMARY KEY (subject_id),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(previous_id) REFERENCES business_subject (id)
);

-- table: plan_task

CREATE TABLE plan_task (
	plan_id VARCHAR(36) NOT NULL,
	key VARCHAR(80) NOT NULL,
	name VARCHAR(150) NOT NULL,
	owner_user_id VARCHAR(36) NOT NULL,
	planned_start DATE NOT NULL,
	planned_end DATE NOT NULL,
	actual_start DATE,
	actual_end DATE,
	status VARCHAR(30) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (plan_id, key),
	CHECK (planned_end >= planned_start),
	FOREIGN KEY(plan_id) REFERENCES business_subject (id),
	FOREIGN KEY(owner_user_id) REFERENCES app_user (id)
);

-- table: price_detail

CREATE TABLE price_detail (
	subject_id VARCHAR(36) NOT NULL,
	supplier_id VARCHAR(36) NOT NULL,
	material_id VARCHAR(36) NOT NULL,
	unit_price NUMERIC(18, 6) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	valid_from DATE NOT NULL,
	valid_to DATE NOT NULL,
	quote_evidence TEXT NOT NULL,
	PRIMARY KEY (subject_id),
	CHECK (unit_price >= 0),
	CHECK (valid_to >= valid_from),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(supplier_id) REFERENCES supplier (id),
	FOREIGN KEY(material_id) REFERENCES material (id)
);

-- table: project_closure_case

CREATE TABLE project_closure_case (
	project_id VARCHAR(36) NOT NULL,
	mode VARCHAR(20) NOT NULL,
	status VARCHAR(20) NOT NULL,
	current_stage VARCHAR(200) NOT NULL,
	opened_by VARCHAR(36) NOT NULL,
	source_termination_subject_id VARCHAR(36),
	version INTEGER NOT NULL,
	closed_by VARCHAR(36),
	closed_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT project_closure_mode CHECK (mode IN ('NORMAL','TERMINATION')),
	CONSTRAINT project_closure_status CHECK (status IN ('OPEN','CLOSED','CANCELLED')),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(opened_by) REFERENCES app_user (id),
	UNIQUE (source_termination_subject_id),
	FOREIGN KEY(source_termination_subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(closed_by) REFERENCES app_user (id)
);

-- table: project_pause_detail

CREATE TABLE project_pause_detail (
	subject_id VARCHAR(36) NOT NULL,
	decision VARCHAR(20) NOT NULL,
	effective_date DATE NOT NULL,
	expected_resume_date DATE,
	reason TEXT NOT NULL,
	evidence TEXT NOT NULL,
	source_pause_subject_id VARCHAR(36),
	plan_subject_id VARCHAR(36),
	task_snapshot JSONB NOT NULL,
	customer_due_date_snapshot DATE,
	PRIMARY KEY (subject_id),
	CONSTRAINT project_pause_decision CHECK (decision IN ('PAUSE','RESUME')),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(source_pause_subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(plan_subject_id) REFERENCES business_subject (id)
);

-- table: purchase_order

CREATE TABLE purchase_order (
	request_id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	supplier_id VARCHAR(36),
	number VARCHAR(80) NOT NULL,
	status VARCHAR(30) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	version INTEGER NOT NULL,
	issued_by VARCHAR(36),
	issued_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (status IN ('DRAFT','ISSUED','CLOSED','CANCELLED')),
	UNIQUE (request_id),
	FOREIGN KEY(request_id) REFERENCES purchase_request (id),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(supplier_id) REFERENCES supplier (id),
	UNIQUE (number),
	FOREIGN KEY(issued_by) REFERENCES app_user (id)
);

-- table: purchase_request_line

CREATE TABLE purchase_request_line (
	request_id VARCHAR(36) NOT NULL,
	material_id VARCHAR(36) NOT NULL,
	quantity NUMERIC(18, 6) NOT NULL,
	due_date DATE NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (quantity > 0),
	FOREIGN KEY(request_id) REFERENCES purchase_request (id),
	FOREIGN KEY(material_id) REFERENCES material (id)
);

-- table: stock_movement

CREATE TABLE stock_movement (
	balance_id VARCHAR(36) NOT NULL,
	quantity NUMERIC(18, 6) NOT NULL,
	kind VARCHAR(30) NOT NULL,
	source_key VARCHAR(150) NOT NULL,
	confirmed_by VARCHAR(36) NOT NULL,
	evidence TEXT NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (quantity <> 0),
	FOREIGN KEY(balance_id) REFERENCES stock_balance (id),
	UNIQUE (source_key),
	FOREIGN KEY(confirmed_by) REFERENCES app_user (id)
);

-- table: trial_detail

CREATE TABLE trial_detail (
	subject_id VARCHAR(36) NOT NULL,
	assembly_id VARCHAR(36) NOT NULL,
	planned_date DATE NOT NULL,
	location VARCHAR(200) NOT NULL,
	acceptance_criteria TEXT NOT NULL,
	responsible_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (subject_id),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(assembly_id) REFERENCES business_subject (id),
	FOREIGN KEY(responsible_id) REFERENCES app_user (id)
);

-- table: trial_result

CREATE TABLE trial_result (
	trial_id VARCHAR(36) NOT NULL,
	passed BOOLEAN NOT NULL,
	actual_date DATE NOT NULL,
	evidence TEXT NOT NULL,
	findings TEXT NOT NULL,
	change_id VARCHAR(36),
	confirmed_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (trial_id),
	FOREIGN KEY(trial_id) REFERENCES business_subject (id),
	FOREIGN KEY(change_id) REFERENCES business_subject (id),
	FOREIGN KEY(confirmed_by) REFERENCES app_user (id)
);

-- table: change_impact

CREATE TABLE change_impact (
	change_id VARCHAR(36) NOT NULL,
	task_id VARCHAR(36) NOT NULL,
	action VARCHAR(30) NOT NULL,
	implemented_by VARCHAR(36),
	implementation_evidence TEXT,
	rechecked_by VARCHAR(36),
	recheck_passed BOOLEAN,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (change_id, task_id),
	CHECK (action IN ('KEEP','PAUSE','CANCEL','REWORK')),
	FOREIGN KEY(change_id) REFERENCES business_subject (id),
	FOREIGN KEY(task_id) REFERENCES plan_task (id),
	FOREIGN KEY(implemented_by) REFERENCES app_user (id),
	FOREIGN KEY(rechecked_by) REFERENCES app_user (id)
);

-- table: contact_attachment

CREATE TABLE contact_attachment (
	case_id VARCHAR(36) NOT NULL,
	file_id VARCHAR(36) NOT NULL,
	document_id VARCHAR(36) NOT NULL,
	version INTEGER NOT NULL,
	title VARCHAR(150) NOT NULL,
	previous_id VARCHAR(36),
	created_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (case_id, document_id, version),
	UNIQUE (case_id, file_id),
	CHECK (version > 0),
	FOREIGN KEY(case_id) REFERENCES contact_case (id),
	FOREIGN KEY(file_id) REFERENCES file_object (id),
	FOREIGN KEY(previous_id) REFERENCES contact_attachment (id),
	FOREIGN KEY(created_by) REFERENCES app_user (id)
);

-- table: contract_signing_record

CREATE TABLE contract_signing_record (
	contract_subject_id VARCHAR(36) NOT NULL,
	template_name VARCHAR(150) NOT NULL,
	signing_method VARCHAR(40) NOT NULL,
	status VARCHAR(30) NOT NULL,
	signed_date DATE,
	signed_file_id VARCHAR(36),
	signed_file_title VARCHAR(200) NOT NULL,
	supplier_signer VARCHAR(120) NOT NULL,
	buyer_reviewer_id VARCHAR(36),
	approved_by VARCHAR(36),
	evidence TEXT NOT NULL,
	source_system VARCHAR(20) NOT NULL,
	source_ref VARCHAR(120),
	recorded_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT contract_signing_record_unique_source UNIQUE (contract_subject_id, status, source_ref),
	CONSTRAINT contract_signing_method CHECK (signing_method IN ('MANUAL','OFFLINE_FILE','IMPORT','ERP','OTHER')),
	CONSTRAINT contract_signing_status CHECK (status IN ('DRAFT','UNDER_REVIEW','SIGNED','REJECTED','CANCELLED')),
	CONSTRAINT contract_signing_source_system CHECK (source_system IN ('MANUAL','IMPORT','ERP')),
	FOREIGN KEY(contract_subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(signed_file_id) REFERENCES file_object (id),
	FOREIGN KEY(buyer_reviewer_id) REFERENCES app_user (id),
	FOREIGN KEY(approved_by) REFERENCES app_user (id),
	FOREIGN KEY(recorded_by) REFERENCES app_user (id)
);

-- table: customer_acceptance_record

CREATE TABLE customer_acceptance_record (
	project_id VARCHAR(36) NOT NULL,
	signature_id VARCHAR(36),
	acceptance_type VARCHAR(30) NOT NULL,
	result VARCHAR(30) NOT NULL,
	accepted_date DATE NOT NULL,
	issue_description TEXT NOT NULL,
	responsibility VARCHAR(40) NOT NULL,
	corrective_due_date DATE,
	contact_case_id VARCHAR(36),
	supplier_id VARCHAR(36),
	deduction_amount NUMERIC(18, 2),
	currency VARCHAR(3),
	schedule_impact_days INTEGER NOT NULL,
	contract_change_required BOOLEAN NOT NULL,
	evidence TEXT NOT NULL,
	confirmed_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT customer_acceptance_type CHECK (acceptance_type IN ('INITIAL','RECHECK')),
	CONSTRAINT customer_acceptance_result CHECK (result IN ('PASSED','FAILED','CONDITIONALLY_PASSED')),
	CONSTRAINT customer_acceptance_responsibility CHECK (responsibility IN ('CUSTOMER','SUPPLIER','INTERNAL','SHARED','UNKNOWN')),
	CONSTRAINT customer_acceptance_deduction_nonnegative CHECK (deduction_amount IS NULL OR deduction_amount >= 0),
	CONSTRAINT customer_acceptance_schedule_impact_nonnegative CHECK (schedule_impact_days >= 0),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(signature_id) REFERENCES customer_delivery_signature (id),
	FOREIGN KEY(contact_case_id) REFERENCES contact_case (id),
	FOREIGN KEY(supplier_id) REFERENCES supplier (id),
	FOREIGN KEY(confirmed_by) REFERENCES app_user (id)
);

-- table: customer_receipt_confirmation

CREATE TABLE customer_receipt_confirmation (
	project_id VARCHAR(36) NOT NULL,
	contract_subject_id VARCHAR(36) NOT NULL,
	stage_id VARCHAR(36),
	amount NUMERIC(18, 2) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	received_date DATE NOT NULL,
	reference VARCHAR(100) NOT NULL,
	evidence TEXT NOT NULL,
	confirmed_by VARCHAR(36) NOT NULL,
	source_system VARCHAR(20) NOT NULL,
	source_ref VARCHAR(120),
	note TEXT,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT customer_receipt_amount_positive CHECK (amount > 0),
	CONSTRAINT customer_receipt_source_system CHECK (source_system IN ('MANUAL','IMPORT','ERP')),
	CONSTRAINT customer_receipt_unique_source UNIQUE (project_id, source_system, source_ref),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(contract_subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(stage_id) REFERENCES payment_stage (id),
	UNIQUE (reference),
	FOREIGN KEY(confirmed_by) REFERENCES app_user (id)
);

-- table: design_item

CREATE TABLE design_item (
	design_id VARCHAR(36) NOT NULL,
	material_id VARCHAR(36) NOT NULL,
	quantity NUMERIC(18, 6) NOT NULL,
	route VARCHAR(30) NOT NULL,
	task_id VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (design_id, material_id),
	CHECK (quantity > 0),
	CHECK (route IN ('INTERNAL','PURCHASE','OUTSOURCE')),
	FOREIGN KEY(design_id) REFERENCES business_subject (id),
	FOREIGN KEY(material_id) REFERENCES material (id),
	FOREIGN KEY(task_id) REFERENCES plan_task (id)
);

-- table: finance_correction_detail

CREATE TABLE finance_correction_detail (
	subject_id VARCHAR(36) NOT NULL,
	original_payment_id VARCHAR(36) NOT NULL,
	reason TEXT NOT NULL,
	reversal_evidence TEXT NOT NULL,
	reversal_date DATE NOT NULL,
	reversal_id VARCHAR(36),
	PRIMARY KEY (subject_id),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(original_payment_id) REFERENCES payment_confirmation (id),
	UNIQUE (reversal_id),
	FOREIGN KEY(reversal_id) REFERENCES payment_confirmation (id)
);

-- table: outsource_change_negotiation

CREATE TABLE outsource_change_negotiation (
	project_id VARCHAR(36) NOT NULL,
	supplier_id VARCHAR(36) NOT NULL,
	contract_subject_id VARCHAR(36),
	contact_case_id VARCHAR(36),
	contact_task_id VARCHAR(36),
	customer_quote_amount NUMERIC(18, 2),
	supplier_quote_amount NUMERIC(18, 2),
	negotiated_amount NUMERIC(18, 2),
	currency VARCHAR(3) NOT NULL,
	schedule_impact_days INTEGER NOT NULL,
	task_impact_summary TEXT NOT NULL,
	requires_contract_change BOOLEAN NOT NULL,
	status VARCHAR(30) NOT NULL,
	customer_evidence TEXT NOT NULL,
	supplier_evidence TEXT NOT NULL,
	negotiation_evidence TEXT NOT NULL,
	approved_by VARCHAR(36),
	source_system VARCHAR(20) NOT NULL,
	source_ref VARCHAR(120),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT outsource_change_negotiation_unique_source UNIQUE (project_id, supplier_id, source_ref),
	CONSTRAINT outsource_change_customer_amount_nonnegative CHECK (customer_quote_amount IS NULL OR customer_quote_amount >= 0),
	CONSTRAINT outsource_change_supplier_amount_nonnegative CHECK (supplier_quote_amount IS NULL OR supplier_quote_amount >= 0),
	CONSTRAINT outsource_change_negotiated_amount_nonnegative CHECK (negotiated_amount IS NULL OR negotiated_amount >= 0),
	CONSTRAINT outsource_change_schedule_impact_nonnegative CHECK (schedule_impact_days >= 0),
	CONSTRAINT outsource_change_negotiation_status CHECK (status IN ('DRAFT','NEGOTIATING','AGREED','APPROVED','CANCELLED')),
	CONSTRAINT outsource_change_negotiation_source_system CHECK (source_system IN ('MANUAL','IMPORT','ERP')),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(supplier_id) REFERENCES supplier (id),
	FOREIGN KEY(contract_subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(contact_case_id) REFERENCES contact_case (id),
	FOREIGN KEY(contact_task_id) REFERENCES contact_task (id),
	FOREIGN KEY(approved_by) REFERENCES app_user (id)
);

-- table: pause_task_shift

CREATE TABLE pause_task_shift (
	pause_id VARCHAR(36) NOT NULL,
	task_id VARCHAR(36) NOT NULL,
	previous_start DATE NOT NULL,
	previous_end DATE NOT NULL,
	shifted_start DATE NOT NULL,
	shifted_end DATE NOT NULL,
	shifted_days INTEGER NOT NULL,
	task_status VARCHAR(30) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (pause_id, task_id),
	CHECK (shifted_days >= 0),
	FOREIGN KEY(pause_id) REFERENCES pause_record (id),
	FOREIGN KEY(task_id) REFERENCES plan_task (id)
);

-- table: payment_request_detail

CREATE TABLE payment_request_detail (
	subject_id VARCHAR(36) NOT NULL,
	stage_id VARCHAR(36) NOT NULL,
	amount NUMERIC(18, 2) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	reservation NUMERIC(18, 2) NOT NULL,
	PRIMARY KEY (subject_id),
	CHECK (amount > 0 AND reservation >= 0 AND reservation <= amount),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(stage_id) REFERENCES payment_stage (id)
);

-- table: project_closure_detail

CREATE TABLE project_closure_detail (
	subject_id VARCHAR(36) NOT NULL,
	decision VARCHAR(30) NOT NULL,
	effective_date DATE NOT NULL,
	reason TEXT NOT NULL,
	evidence TEXT NOT NULL,
	project_version INTEGER NOT NULL,
	closure_case_id VARCHAR(36),
	closure_case_version INTEGER,
	current_stage VARCHAR(200),
	completed_work_summary TEXT,
	incurred_cost_summary TEXT,
	incurred_cost_amount NUMERIC(18, 2),
	currency VARCHAR(3),
	PRIMARY KEY (subject_id),
	CONSTRAINT project_closure_decision CHECK (decision IN ('TERMINATE','NORMAL_CLOSE','SETTLEMENT_CLOSE')),
	CONSTRAINT project_closure_cost CHECK (incurred_cost_amount IS NULL OR incurred_cost_amount >= 0),
	FOREIGN KEY(subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(closure_case_id) REFERENCES project_closure_case (id)
);

-- table: project_closure_item

CREATE TABLE project_closure_item (
	case_id VARCHAR(36) NOT NULL,
	item_key VARCHAR(80) NOT NULL,
	label VARCHAR(200) NOT NULL,
	status VARCHAR(30) NOT NULL,
	allow_not_applicable BOOLEAN NOT NULL,
	system_managed BOOLEAN NOT NULL,
	result TEXT NOT NULL,
	evidence TEXT NOT NULL,
	source_system VARCHAR(20) NOT NULL,
	source_ref VARCHAR(300),
	source_as_of TIMESTAMP WITH TIME ZONE,
	updated_by VARCHAR(36),
	updated_at TIMESTAMP WITH TIME ZONE,
	revision INTEGER NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (case_id, item_key),
	CONSTRAINT project_closure_item_status CHECK (status IN ('PENDING','DONE','NOT_APPLICABLE')),
	CONSTRAINT project_closure_item_source CHECK (source_system IN ('AGENT','ERP','MANUAL')),
	FOREIGN KEY(case_id) REFERENCES project_closure_case (id),
	FOREIGN KEY(updated_by) REFERENCES app_user (id)
);

-- table: purchase_order_line

CREATE TABLE purchase_order_line (
	order_id VARCHAR(36) NOT NULL,
	source_line_id VARCHAR(36) NOT NULL,
	material_id VARCHAR(36) NOT NULL,
	quantity NUMERIC(18, 6) NOT NULL,
	unit_price NUMERIC(18, 6),
	agreed_ship_date DATE NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (quantity > 0 AND (unit_price IS NULL OR unit_price >= 0)),
	FOREIGN KEY(order_id) REFERENCES purchase_order (id),
	UNIQUE (source_line_id),
	FOREIGN KEY(source_line_id) REFERENCES purchase_request_line (id),
	FOREIGN KEY(material_id) REFERENCES material (id)
);

-- table: supplier_deduction_settlement

CREATE TABLE supplier_deduction_settlement (
	project_id VARCHAR(36) NOT NULL,
	supplier_id VARCHAR(36) NOT NULL,
	contract_subject_id VARCHAR(36),
	contact_case_id VARCHAR(36),
	contact_task_id VARCHAR(36),
	reason TEXT NOT NULL,
	responsibility VARCHAR(40) NOT NULL,
	deduction_amount NUMERIC(18, 2) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	status VARCHAR(30) NOT NULL,
	settlement_reference VARCHAR(120),
	responsibility_evidence TEXT NOT NULL,
	settlement_evidence TEXT NOT NULL,
	confirmed_by VARCHAR(36),
	settled_by VARCHAR(36),
	settled_at TIMESTAMP WITH TIME ZONE,
	source_system VARCHAR(20) NOT NULL,
	source_ref VARCHAR(120),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT supplier_deduction_settlement_unique_source UNIQUE (project_id, supplier_id, reason, source_ref),
	CONSTRAINT supplier_deduction_responsibility CHECK (responsibility IN ('CUSTOMER','SUPPLIER','INTERNAL','SHARED','UNKNOWN')),
	CONSTRAINT supplier_deduction_status CHECK (status IN ('PROPOSED','RESPONSIBILITY_CONFIRMED','SETTLED','CANCELLED')),
	CONSTRAINT supplier_deduction_nonnegative CHECK (deduction_amount >= 0),
	CONSTRAINT supplier_deduction_source_system CHECK (source_system IN ('MANUAL','IMPORT','ERP')),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(supplier_id) REFERENCES supplier (id),
	FOREIGN KEY(contract_subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(contact_case_id) REFERENCES contact_case (id),
	FOREIGN KEY(contact_task_id) REFERENCES contact_task (id),
	FOREIGN KEY(confirmed_by) REFERENCES app_user (id),
	FOREIGN KEY(settled_by) REFERENCES app_user (id)
);

-- table: supplier_material_handoff

CREATE TABLE supplier_material_handoff (
	project_id VARCHAR(36) NOT NULL,
	supplier_id VARCHAR(36) NOT NULL,
	contract_subject_id VARCHAR(36),
	file_id VARCHAR(36),
	document_title VARCHAR(200) NOT NULL,
	document_type VARCHAR(40) NOT NULL,
	approval_status VARCHAR(30) NOT NULL,
	provided_date DATE NOT NULL,
	provided_to VARCHAR(150) NOT NULL,
	handoff_channel VARCHAR(40) NOT NULL,
	evidence TEXT NOT NULL,
	source_system VARCHAR(20) NOT NULL,
	source_ref VARCHAR(120),
	provided_by VARCHAR(36) NOT NULL,
	verified_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT supplier_material_handoff_unique_source UNIQUE (project_id, supplier_id, document_title, provided_date, source_ref),
	CONSTRAINT supplier_material_handoff_document_type CHECK (document_type IN ('CUSTOMER_MATERIAL','DESIGN_DRAWING','TECHNICAL_SPEC','QUALITY_STANDARD','OTHER')),
	CONSTRAINT supplier_material_handoff_approval_status CHECK (approval_status IN ('DRAFT','APPROVED','REVOKED')),
	CONSTRAINT supplier_material_handoff_channel CHECK (handoff_channel IN ('MANUAL','EMAIL','IMPORT','ERP','OTHER')),
	CONSTRAINT supplier_material_handoff_source_system CHECK (source_system IN ('MANUAL','IMPORT','ERP')),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(supplier_id) REFERENCES supplier (id),
	FOREIGN KEY(contract_subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(file_id) REFERENCES file_object (id),
	FOREIGN KEY(provided_by) REFERENCES app_user (id),
	FOREIGN KEY(verified_by) REFERENCES app_user (id)
);

-- table: supplier_progress_policy

CREATE TABLE supplier_progress_policy (
	project_id VARCHAR(36) NOT NULL,
	supplier_id VARCHAR(36) NOT NULL,
	contract_subject_id VARCHAR(36) NOT NULL,
	plan_task_id VARCHAR(36),
	stage_key VARCHAR(80) NOT NULL,
	stage_name VARCHAR(150) NOT NULL,
	frequency_days INTEGER NOT NULL,
	effective_from DATE NOT NULL,
	first_due_date DATE NOT NULL,
	evidence_requirements JSONB NOT NULL,
	basis TEXT NOT NULL,
	source_ref VARCHAR(120) NOT NULL,
	version INTEGER NOT NULL,
	active BOOLEAN NOT NULL,
	supersedes_id VARCHAR(36),
	created_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT supplier_progress_policy_version UNIQUE (project_id, supplier_id, contract_subject_id, stage_key, version),
	CONSTRAINT supplier_progress_policy_unique_source UNIQUE (project_id, supplier_id, contract_subject_id, stage_key, source_ref),
	CONSTRAINT supplier_progress_policy_frequency CHECK (frequency_days BETWEEN 1 AND 90),
	CONSTRAINT supplier_progress_policy_version_positive CHECK (version >= 1),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(supplier_id) REFERENCES supplier (id),
	FOREIGN KEY(contract_subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(plan_task_id) REFERENCES plan_task (id),
	FOREIGN KEY(supersedes_id) REFERENCES supplier_progress_policy (id),
	FOREIGN KEY(created_by) REFERENCES app_user (id)
);

-- table: supplier_progress_report

CREATE TABLE supplier_progress_report (
	project_id VARCHAR(36) NOT NULL,
	supplier_id VARCHAR(36) NOT NULL,
	contract_subject_id VARCHAR(36),
	plan_task_id VARCHAR(36),
	stage_key VARCHAR(80) NOT NULL,
	stage_name VARCHAR(150) NOT NULL,
	report_date DATE NOT NULL,
	status VARCHAR(30) NOT NULL,
	progress_percent INTEGER,
	next_due_date DATE,
	issue_summary TEXT NOT NULL,
	evidence TEXT NOT NULL,
	evidence_items JSONB NOT NULL,
	source_system VARCHAR(20) NOT NULL,
	source_ref VARCHAR(120),
	reported_by VARCHAR(36) NOT NULL,
	followed_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT supplier_progress_report_unique_source UNIQUE (project_id, supplier_id, stage_key, report_date, source_ref),
	CONSTRAINT supplier_progress_report_status CHECK (status IN ('ON_TRACK','AT_RISK','BLOCKED','DONE','REWORK')),
	CONSTRAINT supplier_progress_report_progress_range CHECK (progress_percent IS NULL OR progress_percent BETWEEN 0 AND 100),
	CONSTRAINT supplier_progress_report_source_system CHECK (source_system IN ('MANUAL','IMPORT','ERP')),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(supplier_id) REFERENCES supplier (id),
	FOREIGN KEY(contract_subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(plan_task_id) REFERENCES plan_task (id),
	FOREIGN KEY(reported_by) REFERENCES app_user (id),
	FOREIGN KEY(followed_by) REFERENCES app_user (id)
);

-- table: task_dependency

CREATE TABLE task_dependency (
	task_id VARCHAR(36) NOT NULL,
	prerequisite_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (task_id, prerequisite_id),
	CHECK (task_id <> prerequisite_id),
	FOREIGN KEY(task_id) REFERENCES plan_task (id),
	FOREIGN KEY(prerequisite_id) REFERENCES plan_task (id)
);

-- table: delivery_exception

CREATE TABLE delivery_exception (
	order_line_id VARCHAR(36) NOT NULL,
	reason TEXT NOT NULL,
	expected_ship_date DATE,
	status VARCHAR(30) NOT NULL,
	reported_by VARCHAR(36) NOT NULL,
	evidence TEXT NOT NULL,
	closed_by VARCHAR(36),
	resolution TEXT,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (status IN ('OPEN','CLOSED')),
	FOREIGN KEY(order_line_id) REFERENCES purchase_order_line (id),
	FOREIGN KEY(reported_by) REFERENCES app_user (id),
	FOREIGN KEY(closed_by) REFERENCES app_user (id)
);

-- table: order_price_snapshot

CREATE TABLE order_price_snapshot (
	line_id VARCHAR(36) NOT NULL,
	price_subject_id VARCHAR(36) NOT NULL,
	unit_price NUMERIC(18, 6) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	selected_by VARCHAR(36) NOT NULL,
	PRIMARY KEY (line_id),
	FOREIGN KEY(line_id) REFERENCES purchase_order_line (id),
	FOREIGN KEY(price_subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(selected_by) REFERENCES app_user (id)
);

-- table: project_closure_item_revision

CREATE TABLE project_closure_item_revision (
	item_id VARCHAR(36) NOT NULL,
	revision INTEGER NOT NULL,
	from_status VARCHAR(30),
	to_status VARCHAR(30) NOT NULL,
	result TEXT NOT NULL,
	evidence TEXT NOT NULL,
	source_system VARCHAR(20) NOT NULL,
	source_ref VARCHAR(300),
	source_as_of TIMESTAMP WITH TIME ZONE,
	changed_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (item_id, revision),
	FOREIGN KEY(item_id) REFERENCES project_closure_item (id),
	FOREIGN KEY(changed_by) REFERENCES app_user (id)
);

-- table: supplier_material_verification

CREATE TABLE supplier_material_verification (
	project_id VARCHAR(36) NOT NULL,
	supplier_id VARCHAR(36) NOT NULL,
	contract_subject_id VARCHAR(36) NOT NULL,
	handoff_id VARCHAR(36) NOT NULL,
	response_file_id VARCHAR(36),
	response_date DATE NOT NULL,
	result VARCHAR(30) NOT NULL,
	supplier_contact VARCHAR(150) NOT NULL,
	response_channel VARCHAR(40) NOT NULL,
	response_summary TEXT NOT NULL,
	follow_up_due_date DATE,
	evidence TEXT NOT NULL,
	source_system VARCHAR(20) NOT NULL,
	source_ref VARCHAR(120) NOT NULL,
	recorded_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT supplier_material_verification_unique_source UNIQUE (handoff_id, source_ref),
	CONSTRAINT supplier_material_verification_result CHECK (result IN ('RECEIVED','ACCEPTED','NEEDS_CLARIFICATION','REJECTED')),
	CONSTRAINT supplier_material_verification_channel CHECK (response_channel IN ('MANUAL','EMAIL','IMPORT','ERP','OTHER')),
	CONSTRAINT supplier_material_verification_source_system CHECK (source_system IN ('MANUAL','IMPORT','ERP')),
	FOREIGN KEY(project_id) REFERENCES project (id),
	FOREIGN KEY(supplier_id) REFERENCES supplier (id),
	FOREIGN KEY(contract_subject_id) REFERENCES business_subject (id),
	FOREIGN KEY(handoff_id) REFERENCES supplier_material_handoff (id),
	FOREIGN KEY(response_file_id) REFERENCES file_object (id),
	FOREIGN KEY(recorded_by) REFERENCES app_user (id)
);

-- table: supplier_shipment

CREATE TABLE supplier_shipment (
	order_line_id VARCHAR(36) NOT NULL,
	quantity NUMERIC(18, 6) NOT NULL,
	shipped_date DATE NOT NULL,
	reference VARCHAR(150) NOT NULL,
	evidence TEXT NOT NULL,
	confirmed_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (order_line_id, reference),
	CHECK (quantity > 0),
	FOREIGN KEY(order_line_id) REFERENCES purchase_order_line (id),
	FOREIGN KEY(confirmed_by) REFERENCES app_user (id)
);

-- table: goods_receipt

CREATE TABLE goods_receipt (
	shipment_id VARCHAR(36) NOT NULL,
	warehouse_id VARCHAR(36) NOT NULL,
	quantity NUMERIC(18, 6) NOT NULL,
	reference VARCHAR(150) NOT NULL,
	received_by VARCHAR(36) NOT NULL,
	evidence TEXT NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (shipment_id, reference),
	CHECK (quantity > 0),
	FOREIGN KEY(shipment_id) REFERENCES supplier_shipment (id),
	FOREIGN KEY(warehouse_id) REFERENCES warehouse (id),
	FOREIGN KEY(received_by) REFERENCES app_user (id)
);

-- table: receipt_inspection

CREATE TABLE receipt_inspection (
	receipt_id VARCHAR(36) NOT NULL,
	accepted_quantity NUMERIC(18, 6) NOT NULL,
	rejected_quantity NUMERIC(18, 6) NOT NULL,
	inspector_id VARCHAR(36) NOT NULL,
	evidence TEXT NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (accepted_quantity >= 0 AND rejected_quantity >= 0),
	UNIQUE (receipt_id),
	FOREIGN KEY(receipt_id) REFERENCES goods_receipt (id),
	FOREIGN KEY(inspector_id) REFERENCES app_user (id)
);

-- index: ix_business_subject_kind

CREATE INDEX ix_business_subject_kind ON business_subject (kind);

-- index: ix_business_subject_project_id

CREATE INDEX ix_business_subject_project_id ON business_subject (project_id);

-- index: ix_contact_case_project_id

CREATE INDEX ix_contact_case_project_id ON contact_case (project_id);

-- index: ix_logistics_route_project_id

CREATE INDEX ix_logistics_route_project_id ON logistics_route (project_id);

-- index: ix_project_role_member_lookup

CREATE INDEX ix_project_role_member_lookup ON project_role_member (project_id, role_key);

-- index: ix_project_role_member_project_id

CREATE INDEX ix_project_role_member_project_id ON project_role_member (project_id);

-- index: ix_project_role_member_role_key

CREATE INDEX ix_project_role_member_role_key ON project_role_member (role_key);

-- index: ix_project_role_member_user_id

CREATE INDEX ix_project_role_member_user_id ON project_role_member (user_id);

-- index: ix_contact_record_case_id

CREATE INDEX ix_contact_record_case_id ON contact_record (case_id);

-- index: ix_contact_resolution_case_id

CREATE INDEX ix_contact_resolution_case_id ON contact_resolution (case_id);

-- index: ix_contact_task_case_id

CREATE INDEX ix_contact_task_case_id ON contact_task (case_id);

-- index: ix_customer_delivery_signature_project_id

CREATE INDEX ix_customer_delivery_signature_project_id ON customer_delivery_signature (project_id);

-- index: ix_erp_operation_native_id

CREATE INDEX ix_erp_operation_native_id ON erp_operation (native_id);

-- index: ix_logistics_quote_route_id

CREATE INDEX ix_logistics_quote_route_id ON logistics_quote (route_id);

-- index: ix_logistics_quote_settlement_for_project_id

CREATE INDEX ix_logistics_quote_settlement_for_project_id ON logistics_quote (settlement_for_project_id);

-- index: ix_pause_record_project_id

CREATE INDEX ix_pause_record_project_id ON pause_record (project_id);

-- index: uq_pause_record_open_project

CREATE UNIQUE INDEX uq_pause_record_open_project ON pause_record (project_id) WHERE end_date IS NULL;

-- index: ix_payment_confirmation_request_id

CREATE INDEX ix_payment_confirmation_request_id ON payment_confirmation (request_id);

-- index: ix_plan_department_confirmation_plan_change_id

CREATE INDEX ix_plan_department_confirmation_plan_change_id ON plan_department_confirmation (plan_change_id);

-- index: ix_plan_department_confirmation_project_id

CREATE INDEX ix_plan_department_confirmation_project_id ON plan_department_confirmation (project_id);

-- index: ix_plan_task_plan_id

CREATE INDEX ix_plan_task_plan_id ON plan_task (plan_id);

-- index: ix_project_closure_case_project_id

CREATE INDEX ix_project_closure_case_project_id ON project_closure_case (project_id);

-- index: uq_project_closure_open_project

CREATE UNIQUE INDEX uq_project_closure_open_project ON project_closure_case (project_id) WHERE status = 'OPEN';

-- index: ix_purchase_order_project_id

CREATE INDEX ix_purchase_order_project_id ON purchase_order (project_id);

-- index: ix_purchase_request_line_request_id

CREATE INDEX ix_purchase_request_line_request_id ON purchase_request_line (request_id);

-- index: ix_stock_movement_balance_id

CREATE INDEX ix_stock_movement_balance_id ON stock_movement (balance_id);

-- index: ix_change_impact_change_id

CREATE INDEX ix_change_impact_change_id ON change_impact (change_id);

-- index: ix_contact_attachment_case_id

CREATE INDEX ix_contact_attachment_case_id ON contact_attachment (case_id);

-- index: ix_contact_attachment_file_id

CREATE INDEX ix_contact_attachment_file_id ON contact_attachment (file_id);

-- index: ix_contract_signing_record_contract_subject_id

CREATE INDEX ix_contract_signing_record_contract_subject_id ON contract_signing_record (contract_subject_id);

-- index: ix_contract_signing_record_signed_file_id

CREATE INDEX ix_contract_signing_record_signed_file_id ON contract_signing_record (signed_file_id);

-- index: ix_customer_acceptance_record_contact_case_id

CREATE INDEX ix_customer_acceptance_record_contact_case_id ON customer_acceptance_record (contact_case_id);

-- index: ix_customer_acceptance_record_project_id

CREATE INDEX ix_customer_acceptance_record_project_id ON customer_acceptance_record (project_id);

-- index: ix_customer_acceptance_record_signature_id

CREATE INDEX ix_customer_acceptance_record_signature_id ON customer_acceptance_record (signature_id);

-- index: ix_customer_receipt_confirmation_contract_subject_id

CREATE INDEX ix_customer_receipt_confirmation_contract_subject_id ON customer_receipt_confirmation (contract_subject_id);

-- index: ix_customer_receipt_confirmation_project_id

CREATE INDEX ix_customer_receipt_confirmation_project_id ON customer_receipt_confirmation (project_id);

-- index: ix_customer_receipt_confirmation_stage_id

CREATE INDEX ix_customer_receipt_confirmation_stage_id ON customer_receipt_confirmation (stage_id);

-- index: ix_design_item_design_id

CREATE INDEX ix_design_item_design_id ON design_item (design_id);

-- index: ix_outsource_change_negotiation_contact_case_id

CREATE INDEX ix_outsource_change_negotiation_contact_case_id ON outsource_change_negotiation (contact_case_id);

-- index: ix_outsource_change_negotiation_contact_task_id

CREATE INDEX ix_outsource_change_negotiation_contact_task_id ON outsource_change_negotiation (contact_task_id);

-- index: ix_outsource_change_negotiation_contract_subject_id

CREATE INDEX ix_outsource_change_negotiation_contract_subject_id ON outsource_change_negotiation (contract_subject_id);

-- index: ix_outsource_change_negotiation_project_id

CREATE INDEX ix_outsource_change_negotiation_project_id ON outsource_change_negotiation (project_id);

-- index: ix_outsource_change_negotiation_supplier_id

CREATE INDEX ix_outsource_change_negotiation_supplier_id ON outsource_change_negotiation (supplier_id);

-- index: ix_pause_task_shift_pause_id

CREATE INDEX ix_pause_task_shift_pause_id ON pause_task_shift (pause_id);

-- index: ix_pause_task_shift_task_id

CREATE INDEX ix_pause_task_shift_task_id ON pause_task_shift (task_id);

-- index: ix_project_closure_item_case_id

CREATE INDEX ix_project_closure_item_case_id ON project_closure_item (case_id);

-- index: ix_purchase_order_line_order_id

CREATE INDEX ix_purchase_order_line_order_id ON purchase_order_line (order_id);

-- index: ix_supplier_deduction_settlement_contact_case_id

CREATE INDEX ix_supplier_deduction_settlement_contact_case_id ON supplier_deduction_settlement (contact_case_id);

-- index: ix_supplier_deduction_settlement_contact_task_id

CREATE INDEX ix_supplier_deduction_settlement_contact_task_id ON supplier_deduction_settlement (contact_task_id);

-- index: ix_supplier_deduction_settlement_contract_subject_id

CREATE INDEX ix_supplier_deduction_settlement_contract_subject_id ON supplier_deduction_settlement (contract_subject_id);

-- index: ix_supplier_deduction_settlement_project_id

CREATE INDEX ix_supplier_deduction_settlement_project_id ON supplier_deduction_settlement (project_id);

-- index: ix_supplier_deduction_settlement_supplier_id

CREATE INDEX ix_supplier_deduction_settlement_supplier_id ON supplier_deduction_settlement (supplier_id);

-- index: ix_supplier_material_handoff_contract_subject_id

CREATE INDEX ix_supplier_material_handoff_contract_subject_id ON supplier_material_handoff (contract_subject_id);

-- index: ix_supplier_material_handoff_file_id

CREATE INDEX ix_supplier_material_handoff_file_id ON supplier_material_handoff (file_id);

-- index: ix_supplier_material_handoff_project_id

CREATE INDEX ix_supplier_material_handoff_project_id ON supplier_material_handoff (project_id);

-- index: ix_supplier_material_handoff_supplier_id

CREATE INDEX ix_supplier_material_handoff_supplier_id ON supplier_material_handoff (supplier_id);

-- index: ix_supplier_progress_policy_contract_subject_id

CREATE INDEX ix_supplier_progress_policy_contract_subject_id ON supplier_progress_policy (contract_subject_id);

-- index: ix_supplier_progress_policy_plan_task_id

CREATE INDEX ix_supplier_progress_policy_plan_task_id ON supplier_progress_policy (plan_task_id);

-- index: ix_supplier_progress_policy_project_id

CREATE INDEX ix_supplier_progress_policy_project_id ON supplier_progress_policy (project_id);

-- index: ix_supplier_progress_policy_supplier_id

CREATE INDEX ix_supplier_progress_policy_supplier_id ON supplier_progress_policy (supplier_id);

-- index: ix_supplier_progress_report_contract_subject_id

CREATE INDEX ix_supplier_progress_report_contract_subject_id ON supplier_progress_report (contract_subject_id);

-- index: ix_supplier_progress_report_plan_task_id

CREATE INDEX ix_supplier_progress_report_plan_task_id ON supplier_progress_report (plan_task_id);

-- index: ix_supplier_progress_report_project_id

CREATE INDEX ix_supplier_progress_report_project_id ON supplier_progress_report (project_id);

-- index: ix_supplier_progress_report_supplier_id

CREATE INDEX ix_supplier_progress_report_supplier_id ON supplier_progress_report (supplier_id);

-- index: ix_delivery_exception_order_line_id

CREATE INDEX ix_delivery_exception_order_line_id ON delivery_exception (order_line_id);

-- index: ix_project_closure_item_revision_item_id

CREATE INDEX ix_project_closure_item_revision_item_id ON project_closure_item_revision (item_id);

-- index: ix_supplier_material_verification_contract_subject_id

CREATE INDEX ix_supplier_material_verification_contract_subject_id ON supplier_material_verification (contract_subject_id);

-- index: ix_supplier_material_verification_handoff_id

CREATE INDEX ix_supplier_material_verification_handoff_id ON supplier_material_verification (handoff_id);

-- index: ix_supplier_material_verification_project_id

CREATE INDEX ix_supplier_material_verification_project_id ON supplier_material_verification (project_id);

-- index: ix_supplier_material_verification_response_file_id

CREATE INDEX ix_supplier_material_verification_response_file_id ON supplier_material_verification (response_file_id);

-- index: ix_supplier_material_verification_supplier_id

CREATE INDEX ix_supplier_material_verification_supplier_id ON supplier_material_verification (supplier_id);

-- index: ix_supplier_shipment_order_line_id

CREATE INDEX ix_supplier_shipment_order_line_id ON supplier_shipment (order_line_id);

-- index: ix_goods_receipt_shipment_id

CREATE INDEX ix_goods_receipt_shipment_id ON goods_receipt (shipment_id);
