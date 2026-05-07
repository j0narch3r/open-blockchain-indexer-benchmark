import { createConfig } from "ponder";

const factoryAbi = [
  {
    "anonymous": false,
    "inputs": [
      {
        "indexed": true,
        "internalType": "address",
        "name": "token0",
        "type": "address"
      },
      {
        "indexed": true,
        "internalType": "address",
        "name": "token1",
        "type": "address"
      },
      {
        "indexed": false,
        "internalType": "address",
        "name": "pair",
        "type": "address"
      },
      {
        "indexed": false,
        "internalType": "uint256",
        "name": "",
        "type": "uint256"
      }
    ],
    "name": "PairCreated",
    "type": "event"
  }
] as const;

const pairAbi = [
  {
    "anonymous": false,
    "inputs": [
      {
        "indexed": true,
        "internalType": "address",
        "name": "sender",
        "type": "address"
      },
      {
        "indexed": false,
        "internalType": "uint256",
        "name": "amount0In",
        "type": "uint256"
      },
      {
        "indexed": false,
        "internalType": "uint256",
        "name": "amount1In",
        "type": "uint256"
      },
      {
        "indexed": false,
        "internalType": "uint256",
        "name": "amount0Out",
        "type": "uint256"
      },
      {
        "indexed": false,
        "internalType": "uint256",
        "name": "amount1Out",
        "type": "uint256"
      },
      {
        "indexed": true,
        "internalType": "address",
        "name": "to",
        "type": "address"
      }
    ],
    "name": "Swap",
    "type": "event"
  }
] as const;

console.log("process.env.PONDER_RPC_URL_1", process.env.PONDER_RPC_URL_1)

export default createConfig({
  chains: {
    ethereum: {
      id: 1,
      rpc: process.env.PONDER_RPC_URL_1,
    },
  },
  contracts: {
    UniswapV2Factory: {
      chain: "ethereum",
      abi: factoryAbi,
      address: "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
      startBlock: 19000000,
      endBlock: 19010000,
    },
    UniswapV2Pair: {
      chain: "ethereum",
      abi: pairAbi,
      startBlock: 19000000,
      endBlock: 19010000,
      factory: {
        address: "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        event: "PairCreated",
        parameter: "pair",
      },
    },
  },
});
