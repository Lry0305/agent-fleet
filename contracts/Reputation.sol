// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title Reputation
 * @notice Agent 声誉系统 —— 每次交易完成后 Consumer 给 Provider 打分
 *
 * 目的：帮助 Consumer 选择可信赖的服务提供商
 *       多次低分 → Provider 接不到单子 → 经济惩罚
 */
contract Reputation {

    struct ReputationData {
        uint256 totalRatings;    // 评价总次数
        uint256 sumScores;       // 分数总和
        uint256 completedJobs;   // 完成的订单数
    }

    // provider 地址 → 声誉数据
    mapping(address => ReputationData) public reputations;

    // 每个 Consumer 对每个 Provider 只能评价一次
    mapping(address => mapping(address => bool)) public hasRated;

    event ProviderRated(
        address indexed provider,
        address indexed rater,
        uint256 score,
        uint256 newAverage
    );

    // 无需构造函数参数，评分系统独立于支付合约
    constructor() {}

    /**
     * @notice Consumer 给 Provider 打分（1-5 星）
     * @param _provider 供应商地址
     * @param _score 评分 1-5
     */
    function rateProvider(address _provider, uint256 _score) external {
        require(_score >= 1 && _score <= 5, "score must be 1-5");
        require(!hasRated[msg.sender][_provider], "already rated this provider");
        require(_provider != msg.sender, "cannot self-rate");

        reputations[_provider].totalRatings++;
        reputations[_provider].sumScores += _score;
        reputations[_provider].completedJobs++;

        hasRated[msg.sender][_provider] = true;

        uint256 average = reputations[_provider].sumScores /
            reputations[_provider].totalRatings;

        emit ProviderRated(_provider, msg.sender, _score, average);
    }

    /**
     * @notice 查询供应商的平均评分
     */
    function getRating(address _provider)
        external
        view
        returns (uint256 average, uint256 totalRatings, uint256 completedJobs)
    {
        ReputationData memory r = reputations[_provider];
        if (r.totalRatings == 0) {
            return (0, 0, 0);
        }
        average = r.sumScores / r.totalRatings;
        return (average, r.totalRatings, r.completedJobs);
    }
}
