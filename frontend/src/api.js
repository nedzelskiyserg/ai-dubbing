import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:5001/api';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 5000, // Таймаут 5 секунд
  validateStatus: function (status) {
    // Разрешаем любые статусы меньше 500, чтобы обрабатывать ошибки вручную
    return status < 500;
  },
});

export const healthCheck = async (retries = 3, delay = 1000) => {
  for (let i = 0; i < retries; i++) {
    try {
      const response = await api.get('/health', { timeout: 2000 });
      if (response.status === 200) {
        return response.data;
      }
    } catch (error) {
      // Если это последняя попытка, пробрасываем ошибку
      if (i === retries - 1) {
        console.error('Health check failed after all retries:', error);
        throw error;
      }
      // Иначе ждем и пробуем снова
      await new Promise(resolve => setTimeout(resolve, delay));
    }
  }
  throw new Error('Health check failed after all retries');
};

export const getLogs = async () => {
  try {
    const response = await api.get('/logs');
    return response.data.logs;
  } catch (error) {
    console.error('Failed to get logs:', error);
    throw error;
  }
};

export const clearLogs = async () => {
  try {
    const response = await api.post('/logs/clear');
    return response.data;
  } catch (error) {
    console.error('Failed to clear logs:', error);
    throw error;
  }
};

export const processYouTube = async (url, quality, options) => {
  try {
    const response = await api.post('/process/youtube', {
      url,
      quality,
      ...options,
    });
    
    // Проверяем статус ответа
    if (response.status >= 400) {
      const error = new Error(response.data?.error || `HTTP ${response.status}`);
      error.response = response;
      throw error;
    }
    
    return response.data;
  } catch (error) {
    console.error('Failed to process YouTube:', error);
    
    // Если это ошибка axios, пробрасываем её дальше с полной информацией
    if (error.response) {
      // Сервер ответил с кодом ошибки
      const customError = new Error(error.response.data?.error || `Server error: ${error.response.status}`);
      customError.response = error.response;
      throw customError;
    } else if (error.request) {
      // Запрос отправлен, но ответа нет
      const customError = new Error('Server is not responding');
      customError.request = error.request;
      customError.code = error.code;
      throw customError;
    }
    
    throw error;
  }
};

export const processFile = async (file, options) => {
  try {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('options', JSON.stringify(options));
    
    const response = await api.post('/process/file', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 300000, // 5 минут для загрузки больших файлов
    });
    
    // Проверяем статус ответа
    if (response.status >= 400) {
      const error = new Error(response.data?.error || `HTTP ${response.status}`);
      error.response = response;
      throw error;
    }
    
    return response.data;
  } catch (error) {
    console.error('Failed to process file:', error);
    
    // Если это ошибка axios, пробрасываем её дальше с полной информацией
    if (error.response) {
      // Сервер ответил с кодом ошибки
      const customError = new Error(error.response.data?.error || `Server error: ${error.response.status}`);
      customError.response = error.response;
      throw customError;
    } else if (error.request) {
      // Запрос отправлен, но ответа нет
      const customError = new Error('Server is not responding');
      customError.request = error.request;
      customError.code = error.code;
      throw customError;
    } else if (error.code === 'ECONNABORTED') {
      // Таймаут
      const customError = new Error('Request timeout. The file might be too large.');
      customError.code = error.code;
      throw customError;
    }
    
    throw error;
  }
};

export const getStatus = async () => {
  try {
    const response = await api.get('/status');
    return response.data;
  } catch (error) {
    console.error('Failed to get status:', error);
    throw error;
  }
};

export const openFolder = async () => {
  try {
    const response = await api.post('/open-folder');
    return response.data;
  } catch (error) {
    console.error('Failed to open folder:', error);
    throw error;
  }
};

export const stopProcessing = async () => {
  try {
    const response = await api.post('/stop');
    return response.data;
  } catch (error) {
    console.error('Failed to stop processing:', error);
    throw error;
  }
};

export default api;
