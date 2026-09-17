-- Frozen PostgreSQL DDL for generic host revision a10c0e000001.

-- table: app_user

CREATE TABLE app_user (
	username VARCHAR(80) NOT NULL,
	display_name VARCHAR(100) NOT NULL,
	department VARCHAR(100) NOT NULL,
	password_hash TEXT NOT NULL,
	super_admin BOOLEAN NOT NULL,
	active BOOLEAN NOT NULL,
	security_version INTEGER NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (username)
);

-- table: assignment_group

CREATE TABLE assignment_group (
	kind VARCHAR(20) NOT NULL,
	name VARCHAR(100) NOT NULL,
	active BOOLEAN NOT NULL,
	version INTEGER NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (kind, name),
	CHECK (kind IN ('ROLE','DEPARTMENT'))
);

-- table: material_template

CREATE TABLE material_template (
	template_key VARCHAR(80) NOT NULL,
	version INTEGER NOT NULL,
	name VARCHAR(150) NOT NULL,
	status VARCHAR(20) NOT NULL,
	contract JSONB NOT NULL,
	package_hash VARCHAR(64) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (template_key, version),
	CHECK (status IN ('DRAFT','PUBLISHED'))
);

-- table: outbox_event

CREATE TABLE outbox_event (
	kind VARCHAR(80) NOT NULL,
	resource_id VARCHAR(100) NOT NULL,
	payload JSONB NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	attempts INTEGER NOT NULL,
	lease_id VARCHAR(36),
	lease_until TIMESTAMP WITH TIME ZONE,
	retry_at TIMESTAMP WITH TIME ZONE,
	last_error VARCHAR(80),
	dead_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

-- table: workflow_category

CREATE TABLE workflow_category (
	name VARCHAR(100) NOT NULL,
	active BOOLEAN NOT NULL,
	version INTEGER NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

-- table: agent_approval_delegation

CREATE TABLE agent_approval_delegation (
	user_id VARCHAR(36) NOT NULL,
	process_key VARCHAR(80) NOT NULL,
	node_key VARCHAR(80) NOT NULL,
	decision VARCHAR(20) NOT NULL,
	active BOOLEAN NOT NULL,
	reason TEXT NOT NULL,
	valid_from TIMESTAMP WITH TIME ZONE,
	valid_to TIMESTAMP WITH TIME ZONE,
	created_by VARCHAR(36) NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	revoked_by VARCHAR(36),
	revoke_reason TEXT,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (user_id, process_key, node_key, decision),
	CONSTRAINT agent_approval_delegation_decision CHECK (decision IN ('APPROVE')),
	FOREIGN KEY(user_id) REFERENCES app_user (id),
	FOREIGN KEY(created_by) REFERENCES app_user (id),
	FOREIGN KEY(revoked_by) REFERENCES app_user (id)
);

CREATE INDEX ix_agent_approval_delegation_lookup ON agent_approval_delegation (process_key, node_key, active);

CREATE INDEX ix_agent_approval_delegation_user_id ON agent_approval_delegation (user_id);

-- table: agent_capability_assignment

CREATE TABLE agent_capability_assignment (
	user_id VARCHAR(36) NOT NULL,
	kind VARCHAR(10) NOT NULL,
	key VARCHAR(100) NOT NULL,
	enabled BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (user_id, kind, key),
	FOREIGN KEY(user_id) REFERENCES app_user (id)
);

CREATE INDEX ix_agent_capability_assignment_user_id ON agent_capability_assignment (user_id);

-- table: ai_conversation

CREATE TABLE ai_conversation (
	user_id VARCHAR(36) NOT NULL,
	title VARCHAR(150) NOT NULL,
	pinned BOOLEAN NOT NULL,
	archived BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES app_user (id)
);

CREATE INDEX ix_ai_conversation_user_id ON ai_conversation (user_id);

-- table: app_user_profile

CREATE TABLE app_user_profile (
	user_id VARCHAR(36) NOT NULL,
	avatar_url TEXT NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (user_id),
	FOREIGN KEY(user_id) REFERENCES app_user (id) ON DELETE CASCADE
);

-- table: assignment_member

CREATE TABLE assignment_member (
	group_id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	is_head BOOLEAN NOT NULL,
	PRIMARY KEY (group_id, user_id),
	FOREIGN KEY(group_id) REFERENCES assignment_group (id),
	FOREIGN KEY(user_id) REFERENCES app_user (id)
);

-- table: audit_event

CREATE TABLE audit_event (
	user_id VARCHAR(36),
	action VARCHAR(100) NOT NULL,
	resource_id VARCHAR(100) NOT NULL,
	detail JSONB NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES app_user (id)
);

-- table: human_action_intent

CREATE TABLE human_action_intent (
	user_id VARCHAR(36) NOT NULL,
	action VARCHAR(80) NOT NULL,
	resource_id VARCHAR(36) NOT NULL,
	payload JSONB NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	challenge_hash VARCHAR(64) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	receipt JSONB,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES app_user (id)
);

-- table: inbox_event

CREATE TABLE inbox_event (
	event_id VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (event_id),
	FOREIGN KEY(event_id) REFERENCES outbox_event (id)
);

-- table: login_session

CREATE TABLE login_session (
	token_hash VARCHAR(64) NOT NULL,
	csrf_hash VARCHAR(64) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (token_hash),
	FOREIGN KEY(user_id) REFERENCES app_user (id)
);

-- table: material_template_xlsx_mapping

CREATE TABLE material_template_xlsx_mapping (
	template_id VARCHAR(36) NOT NULL,
	version INTEGER NOT NULL,
	name VARCHAR(150) NOT NULL,
	mapping JSONB NOT NULL,
	mapping_hash VARCHAR(64) NOT NULL,
	created_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (template_id, version),
	FOREIGN KEY(template_id) REFERENCES material_template (id),
	FOREIGN KEY(created_by) REFERENCES app_user (id)
);

CREATE INDEX ix_material_template_xlsx_mapping_template_id ON material_template_xlsx_mapping (template_id);

-- table: notification

CREATE TABLE notification (
	event_id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	title VARCHAR(150) NOT NULL,
	resource_id VARCHAR(100) NOT NULL,
	read BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (event_id, user_id),
	FOREIGN KEY(event_id) REFERENCES outbox_event (id),
	FOREIGN KEY(user_id) REFERENCES app_user (id)
);

-- table: permission_grant

CREATE TABLE permission_grant (
	user_id VARCHAR(36) NOT NULL,
	permission VARCHAR(100) NOT NULL,
	effect VARCHAR(5) NOT NULL,
	scope JSONB NOT NULL,
	fields JSONB NOT NULL,
	valid_from TIMESTAMP WITH TIME ZONE,
	valid_to TIMESTAMP WITH TIME ZONE,
	active BOOLEAN NOT NULL,
	reason TEXT NOT NULL,
	granted_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (effect IN ('ALLOW','DENY')),
	FOREIGN KEY(user_id) REFERENCES app_user (id),
	FOREIGN KEY(granted_by) REFERENCES app_user (id)
);

CREATE INDEX ix_permission_grant_user_id ON permission_grant (user_id);

-- table: workflow_definition

CREATE TABLE workflow_definition (
	process_key VARCHAR(80) NOT NULL,
	version INTEGER NOT NULL,
	name VARCHAR(150) NOT NULL,
	category_id VARCHAR(36),
	material_template_id VARCHAR(36),
	status VARCHAR(30) NOT NULL,
	config JSONB NOT NULL,
	bpmn_xml TEXT NOT NULL,
	package_hash VARCHAR(64) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (process_key, version),
	FOREIGN KEY(category_id) REFERENCES workflow_category (id),
	FOREIGN KEY(material_template_id) REFERENCES material_template (id)
);

-- table: ai_run

CREATE TABLE ai_run (
	conversation_id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	security_version INTEGER NOT NULL,
	prompt TEXT NOT NULL,
	status VARCHAR(40) NOT NULL,
	checkpoint JSONB NOT NULL,
	result JSONB,
	lease_epoch INTEGER NOT NULL,
	lease_until TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(conversation_id) REFERENCES ai_conversation (id),
	FOREIGN KEY(user_id) REFERENCES app_user (id)
);

-- table: approval_instance

CREATE TABLE approval_instance (
	resource_type VARCHAR(80) NOT NULL,
	resource_id VARCHAR(36) NOT NULL,
	definition_id VARCHAR(36) NOT NULL,
	revision INTEGER NOT NULL,
	round_no INTEGER NOT NULL,
	status VARCHAR(30) NOT NULL,
	incident TEXT,
	stage_index INTEGER NOT NULL,
	version INTEGER NOT NULL,
	snapshot JSONB NOT NULL,
	snapshot_hash VARCHAR(64) NOT NULL,
	engine_state JSONB NOT NULL,
	assignment_snapshots JSONB NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (resource_type, resource_id, revision, round_no),
	CONSTRAINT approval_resource_type_required CHECK (resource_type <> ''),
	CONSTRAINT approval_resource_id_required CHECK (resource_id <> ''),
	FOREIGN KEY(definition_id) REFERENCES workflow_definition (id)
);

CREATE INDEX ix_approval_instance_resource_id ON approval_instance (resource_id);

CREATE INDEX ix_approval_instance_resource_type ON approval_instance (resource_type);

-- table: file_object

CREATE TABLE file_object (
	owner_id VARCHAR(36) NOT NULL,
	conversation_id VARCHAR(36) NOT NULL,
	request_key VARCHAR(36) NOT NULL,
	filename VARCHAR(200) NOT NULL,
	media_type VARCHAR(120) NOT NULL,
	size INTEGER NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	backend VARCHAR(10) NOT NULL,
	storage_namespace VARCHAR(200) NOT NULL,
	object_key VARCHAR(150) NOT NULL,
	storage_version VARCHAR(1024),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, request_key),
	CHECK (size > 0),
	CHECK (backend IN ('local','s3')),
	FOREIGN KEY(owner_id) REFERENCES app_user (id),
	FOREIGN KEY(conversation_id) REFERENCES ai_conversation (id),
	UNIQUE (object_key)
);

CREATE INDEX ix_file_object_conversation_id ON file_object (conversation_id);

CREATE INDEX ix_file_object_owner_id ON file_object (owner_id);

-- table: agent_run_file

CREATE TABLE agent_run_file (
	run_id VARCHAR(36) NOT NULL,
	file_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (run_id, file_id),
	FOREIGN KEY(run_id) REFERENCES ai_run (id),
	FOREIGN KEY(file_id) REFERENCES file_object (id)
);

-- table: ai_step

CREATE TABLE ai_step (
	run_id VARCHAR(36) NOT NULL,
	sequence INTEGER NOT NULL,
	tool VARCHAR(100) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	result JSONB NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (run_id, sequence),
	FOREIGN KEY(run_id) REFERENCES ai_run (id)
);

-- table: approval_seat

CREATE TABLE approval_seat (
	instance_id VARCHAR(36) NOT NULL,
	stage_index INTEGER NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	status VARCHAR(30) NOT NULL,
	version INTEGER NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (instance_id, stage_index, user_id),
	FOREIGN KEY(instance_id) REFERENCES approval_instance (id),
	FOREIGN KEY(user_id) REFERENCES app_user (id)
);

CREATE INDEX ix_approval_seat_instance_id ON approval_seat (instance_id);

-- table: material_review

CREATE TABLE material_review (
	template_id VARCHAR(36) NOT NULL,
	mapping_id VARCHAR(36),
	file_id VARCHAR(36) NOT NULL,
	owner_id VARCHAR(36) NOT NULL,
	status VARCHAR(30) NOT NULL,
	material_data JSONB NOT NULL,
	issues JSONB NOT NULL,
	template_hash VARCHAR(64) NOT NULL,
	mapping_hash VARCHAR(64) NOT NULL,
	file_sha256 VARCHAR(64) NOT NULL,
	review_hash VARCHAR(64) NOT NULL,
	confirmed_by VARCHAR(36),
	confirmed_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (status IN ('NEEDS_REVIEW','READY_FOR_CONFIRMATION','CONFIRMED','REJECTED')),
	FOREIGN KEY(template_id) REFERENCES material_template (id),
	FOREIGN KEY(mapping_id) REFERENCES material_template_xlsx_mapping (id),
	FOREIGN KEY(file_id) REFERENCES file_object (id),
	FOREIGN KEY(owner_id) REFERENCES app_user (id),
	FOREIGN KEY(confirmed_by) REFERENCES app_user (id)
);

CREATE INDEX ix_material_review_file_id ON material_review (file_id);

CREATE INDEX ix_material_review_mapping_id ON material_review (mapping_id);

CREATE INDEX ix_material_review_owner_id ON material_review (owner_id);

CREATE INDEX ix_material_review_template_id ON material_review (template_id);

-- table: approval_action

CREATE TABLE approval_action (
	instance_id VARCHAR(36) NOT NULL,
	seat_id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	user_snapshot JSONB NOT NULL,
	decision VARCHAR(20) NOT NULL,
	comment TEXT NOT NULL,
	snapshot_hash VARCHAR(64) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(instance_id) REFERENCES approval_instance (id),
	UNIQUE (seat_id),
	FOREIGN KEY(seat_id) REFERENCES approval_seat (id),
	FOREIGN KEY(user_id) REFERENCES app_user (id)
);

-- table: material_binding

CREATE TABLE material_binding (
	resource_type VARCHAR(30) NOT NULL,
	resource_id VARCHAR(36) NOT NULL,
	resource_revision INTEGER NOT NULL,
	definition_id VARCHAR(36) NOT NULL,
	template_id VARCHAR(36) NOT NULL,
	review_id VARCHAR(36) NOT NULL,
	material_hash VARCHAR(64) NOT NULL,
	review_hash VARCHAR(64) NOT NULL,
	bound_by VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(definition_id) REFERENCES workflow_definition (id),
	FOREIGN KEY(template_id) REFERENCES material_template (id),
	FOREIGN KEY(review_id) REFERENCES material_review (id),
	FOREIGN KEY(bound_by) REFERENCES app_user (id)
);

CREATE INDEX ix_material_binding_bound_by ON material_binding (bound_by);

CREATE INDEX ix_material_binding_definition_id ON material_binding (definition_id);

CREATE INDEX ix_material_binding_resource_id ON material_binding (resource_id);

CREATE INDEX ix_material_binding_resource_type ON material_binding (resource_type);

CREATE INDEX ix_material_binding_review_id ON material_binding (review_id);

CREATE INDEX ix_material_binding_template_id ON material_binding (template_id);
