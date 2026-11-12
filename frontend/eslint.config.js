import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import importPlugin from "eslint-plugin-import";

const files = ["**/*.{js,jsx}"];

export default [
  { ignores: ["build/**", "dist/**", "node_modules/**"] },
  { ...js.configs.recommended, files },
  { ...react.configs.flat.recommended, files },
  { ...jsxA11y.flatConfigs.recommended, files },
  {
    files,
    plugins: {
      "react-hooks": reactHooks,
      import: importPlugin,
    },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.node,
      },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    settings: {
      react: { version: "detect" },
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react/react-in-jsx-scope": "off",
      "react/prop-types": "off",
      "no-unused-vars": [
        "warn",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      "jsx-a11y/no-autofocus": "off",
    },
  },
];
