-- Tabelas "migradas" do Synapse para o Unity Catalog, espelhando demo/seed_purview.py.
-- Rode em um SQL warehouse ou notebook. Recria tudo do zero (CREATE OR REPLACE).
--
-- Casos de borda propositais:
--   sales.dimcustomer     colunas em PascalCase (como no Synapse) + tag pré-existente owner_team (preservada)
--   sales.factsales       colunas em minúsculas (match case-insensitive) + comentário pré-existente -> CONFLICT de comentário
--   sales.dimproduct      não tem a coluna LegacyCode (removida também no Purview)
--   sales.daily_targets   só existe no UC -> UC_TABLE_WITHOUT_PURVIEW_ASSET
--   hr.employee           não tem a coluna BadgeLegacy -> COLUMN_NOT_IN_UC
--                         + tag pré-existente sensibilidade='interna' em Salary (alias quer 'restrita') -> CONFLICT de tag
--   hr.vw_employeedirectory  VIEW (comentário de coluna usa COMMENT ON COLUMN)
--   web.clickstream_events   renomeada na migração (web_events) -> resolvida pelo table_overrides.csv
--   hr.LegacyPayroll (Purview) não foi migrada -> UC_TABLE_NOT_FOUND

-- Pré-requisito: o catálogo p2uc_demo. Se o metastore tiver storage root:
--   CREATE CATALOG IF NOT EXISTS p2uc_demo;
-- Senão, informe um caminho de um external location existente:
--   CREATE CATALOG IF NOT EXISTS p2uc_demo MANAGED LOCATION 'abfss://<container>@<conta>.dfs.core.windows.net/p2uc_demo';
CREATE SCHEMA IF NOT EXISTS p2uc_demo.sales;
CREATE SCHEMA IF NOT EXISTS p2uc_demo.hr;
CREATE SCHEMA IF NOT EXISTS p2uc_demo.web;

CREATE OR REPLACE TABLE p2uc_demo.sales.dimcustomer (
  CustomerID INT, FullName STRING, Email STRING, CPF STRING, BirthDate DATE, City STRING
);
ALTER TABLE p2uc_demo.sales.dimcustomer SET TAGS ('owner_team' = 'vendas');

CREATE OR REPLACE TABLE p2uc_demo.sales.factsales (
  saleid BIGINT, customerid INT, productid INT, saledate DATE, netamount DECIMAL(18,2), cardnumber STRING
) COMMENT 'Tabela de vendas (comentário gerado por IA)';

CREATE OR REPLACE TABLE p2uc_demo.sales.dimproduct (
  ProductID INT, ProductName STRING, Category STRING
);

CREATE OR REPLACE TABLE p2uc_demo.sales.daily_targets (
  target_date DATE, store_id INT, target_amount DECIMAL(18,2)
);

CREATE OR REPLACE TABLE p2uc_demo.hr.employee (
  EmployeeID INT, Name STRING, Email STRING, Salary DECIMAL(18,2), HireDate DATE, DepartmentID INT
);
ALTER TABLE p2uc_demo.hr.employee ALTER COLUMN Salary SET TAGS ('sensibilidade' = 'interna');

CREATE OR REPLACE TABLE p2uc_demo.hr.department (
  DepartmentID INT, DepartmentName STRING
);

CREATE OR REPLACE VIEW p2uc_demo.hr.vw_employeedirectory AS
SELECT e.EmployeeID, e.Name, d.DepartmentName
FROM p2uc_demo.hr.employee e LEFT JOIN p2uc_demo.hr.department d ON e.DepartmentID = d.DepartmentID;

CREATE OR REPLACE TABLE p2uc_demo.web.clickstream_events (
  event_id STRING, session_id STRING, ip_address STRING, event_ts TIMESTAMP, page_url STRING
);
