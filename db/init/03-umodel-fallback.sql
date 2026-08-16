-- UnifiedModel 语义运行时退化路径（04 §2 替换成本列声明：SQL 视图兜底）
-- 将 03 §3 的 Node/Link 语义模型以视图实现，query_related_graph（API-M-02）直接查 v_graph_edge
-- 接入 UnifiedModel 运行时后，仅需替换 mcp-core 的查询后端，视图保留作对账

-- 边表：主体间关系（SAME_PAYEE / SAME_DEVICE / SAME_IPSEG / SAME_CONTACT，03 §3 四类边全落地）
CREATE OR REPLACE VIEW v_graph_edge AS
SELECT DISTINCT
  t1.account_hash            AS src_node,
  t2.account_hash            AS dst_node,
  'SAME_PAYEE'::varchar(16)  AS edge_type,
  count(*)                   AS weight
FROM transaction t1
JOIN transaction t2
  ON t2.payee_hash = t1.payee_hash
 AND t2.account_hash <> t1.account_hash
 AND t1.payee_hash IS NOT NULL
GROUP BY t1.account_hash, t2.account_hash
UNION ALL
SELECT DISTINCT
  t1.account_hash, t2.account_hash, 'SAME_DEVICE', count(*)
FROM transaction t1
JOIN transaction t2
  ON t2.device_fp_hash = t1.device_fp_hash
 AND t2.account_hash <> t1.account_hash
 AND t1.device_fp_hash IS NOT NULL
GROUP BY t1.account_hash, t2.account_hash
UNION ALL
SELECT DISTINCT
  t1.account_hash, t2.account_hash, 'SAME_IPSEG', count(*)
FROM transaction t1
JOIN transaction t2
  ON host(network(set_masklen(t1.ip, 24))) = host(network(set_masklen(t2.ip, 24)))
 AND t2.account_hash <> t1.account_hash
 AND t1.ip IS NOT NULL
GROUP BY t1.account_hash, t2.account_hash
UNION ALL
-- 第四类边：同联系方式（主体档案属性 DA-T-01 contact_hash，非交易报文携带，权重恒 1）
SELECT DISTINCT
  a1.account_hash, a2.account_hash, 'SAME_CONTACT', 1::bigint
FROM account a1
JOIN account a2
  ON a2.contact_hash = a1.contact_hash
 AND a2.account_hash <> a1.account_hash
 AND a1.contact_hash IS NOT NULL;

GRANT SELECT ON v_graph_edge TO tg_web, tg_app;

-- 2 跳邻居查询函数（图扩展深度上限 2 跳，AA-SK-02 安全边界）
CREATE OR REPLACE FUNCTION fn_related_graph(p_account char(64), p_hops int DEFAULT 2)
RETURNS TABLE(src_node char(64), dst_node char(64), edge_type varchar(16), weight bigint, hop int)
LANGUAGE sql STABLE AS $$
  WITH RECURSIVE walk AS (
    SELECT e.src_node, e.dst_node, e.edge_type, e.weight, 1 AS hop
    FROM v_graph_edge e WHERE e.src_node = p_account
    UNION ALL
    SELECT e.src_node, e.dst_node, e.edge_type, e.weight, w.hop + 1
    FROM v_graph_edge e JOIN walk w ON e.src_node = w.dst_node
    WHERE w.hop < LEAST(p_hops, 2) AND e.dst_node <> p_account
  )
  SELECT * FROM walk;
$$;

GRANT EXECUTE ON FUNCTION fn_related_graph(char(64), int) TO tg_web, tg_app;

-- 团伙发现（阶段3，R-43）：有限深度 BFS 扩展团伙邻域（默认 3 跳，比 2 跳更广）。
-- 性能：v_graph_edge 是视图（SAME_PAYEE 边 payee_hash 无索引致笛卡尔 452 万边），
-- 递归实时重算超时；故用物化表 mv_graph_edge + src/dst 索引（实测 60s→0.17s）。
-- 物化表为快照，数据演进后需 REFRESH MATERIALIZED VIEW（生产可定时/触发器刷新）。
CREATE OR REPLACE FUNCTION fn_fraud_ring(p_account char(64), p_max_hops int DEFAULT 3)
RETURNS TABLE(node char(64), ring_size bigint)
LANGUAGE sql STABLE AS $$
  WITH RECURSIVE ring AS (
    SELECT p_account AS node, 0 AS depth
    UNION
    SELECT e.dst_node, r.depth + 1
    FROM (SELECT src_node, dst_node FROM mv_graph_edge
          UNION ALL
          SELECT dst_node, src_node FROM mv_graph_edge) e
    JOIN ring r ON e.src_node = r.node
    WHERE r.depth < p_max_hops
  )
  SELECT node, count(*) OVER () AS ring_size FROM ring;
$$;

GRANT EXECUTE ON FUNCTION fn_fraud_ring(char(64), int) TO tg_web, tg_app;

-- 物化边表（阶段3 R-43）：v_graph_edge 视图递归遍历反复重算 452 万边超时，
-- 物化 + src/dst 索引后图遍历可用（实测 60s→0.17s）。快照需 REFRESH。
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_graph_edge AS SELECT * FROM v_graph_edge;
CREATE INDEX IF NOT EXISTS idx_mv_edge_src ON mv_graph_edge (src_node);
CREATE INDEX IF NOT EXISTS idx_mv_edge_dst ON mv_graph_edge (dst_node);
GRANT SELECT ON mv_graph_edge TO tg_web, tg_app;
