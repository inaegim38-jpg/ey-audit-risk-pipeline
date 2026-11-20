-- ============================================================================
-- 04_demo_review.sql : [시연용 처리] 검토 상태 변경 예시
-- 실제로 검토한 결과가 아니라, 상태 추적 기능이 동작하는지 보여 주기 위한 시연입니다.
-- 이 처리 때문에 대시보드의 '해결률'이 실제 업무 성과처럼 보이면 안 됩니다.
-- ============================================================================
WITH picked AS (
    SELECT finding_key, ROW_NUMBER() OVER (ORDER BY risk_score DESC, finding_key) AS rn
    FROM audit_risk.dq_anomaly_findings
    WHERE issue_status = 'OPEN' AND severity = 'CRITICAL'
    LIMIT 3
)
SELECT audit_risk.set_issue_status(
           finding_key,
           CASE rn WHEN 1 THEN 'IN_REVIEW' WHEN 2 THEN 'RESOLVED' ELSE 'FALSE_POSITIVE' END,
           'demo_user',
           '[시연용 처리] 상태 추적 기능 확인용. 실제 검토 결과가 아님')
FROM picked;
